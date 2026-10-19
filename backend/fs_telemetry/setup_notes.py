"""Setup notes: a dated log for each setup in the library - what changed, why,
and what the driver said - readable without the app.

The app keeps the log in setups/notes.json next to the library (JSON is the
*source of truth*), and after every change writes it out again as plain Markdown
in setups/notes/, one file per setup (all versions of one car + track + setup
name), plus an index. Teammates can open those in any text editor, on GitHub or
in Obsidian (each file starts with YAML properties). They're rewritten by the
app, so edits made in the files themselves don't last: notes are added on the
Setups page. The same log downloads as a CSV for Excel.

Why not read the Markdown back? Free-form text that people edit by hand can't be
turned back into entries reliably (a missing dash or heading and notes would
silently vanish or merge); one owner of the data keeps it safe.

Each version's own description (the library's "notes" field) and the setting
changes from the version before are written into the Markdown too, so each file
tells the whole story of that setup.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from .setups import SetupLibrary, SetupSummary

# The API answers requests on several threads at once: one lock for the log file and its Markdown
_LOCK = threading.Lock()
GENERATED = "FS Telemetry setup notes"  # in each Markdown file's properties: marks files the app may rewrite or remove
INDEX_NAME = "Setup notes.md"


class SetupNoteIn(BaseModel):
    """One entry, as sent from the Setups page."""

    date: str = Field(default="", description="Day it's about, YYYY-MM-DD (today if empty)", max_length=10)
    author: str = Field(default="", max_length=60)
    changed: str = Field(default="", max_length=2000, description="What changed")
    why: str = Field(default="", max_length=2000)
    feedback: str = Field(default="", max_length=2000, description="What the driver said")


class SetupNote(SetupNoteIn):
    id: str
    added: str  # ISO time it was written


class SettingChange(BaseModel):
    label: str
    group: str
    before: float | int | str | None
    after: float | int | str | None


class SetupNotesOverview(BaseModel):
    folder: str  # where the Markdown files are, to show on the Setups page
    entries: dict[str, list[SetupNote]]  # by setup id, newest first


def _today_or(text: str) -> str:
    try:
        return date.fromisoformat(text.strip()).isoformat()
    except ValueError:
        return date.today().isoformat()


def _file_name(car: str, track: str, name: str) -> str:
    """A file name Windows accepts: no \\ / : * ? " < > |"""
    return re.sub(r'[\\/:*?"<>|]+', "-", f"{car} - {track} - {name}").strip(" .") + ".md"


def _yaml(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)  # a JSON string is a valid YAML string


def _fmt(v) -> str:
    return "–" if v is None else str(v)


def _quote(text: str) -> str:
    """Multi-line text, kept inside its Markdown list item."""
    return "\n    ".join(text.strip().splitlines())


class SetupNotes:
    def __init__(self, library: SetupLibrary, path: Path | None = None, md_dir: Path | None = None):
        self.library = library
        self.path = path or library.path.with_name("notes.json")
        self.md_dir = md_dir or library.path.with_name("notes")

    # --- storage: {setup id: [entries]}, written atomically like the library
    def _read(self) -> dict[str, list[SetupNote]]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return {k: [SetupNote(**e) for e in v] for k, v in raw.items()}

    def _write(self, notes: dict[str, list[SetupNote]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({k: [e.model_dump() for e in v] for k, v in notes.items() if v}, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)

    @staticmethod
    def _sorted(entries: list[SetupNote]) -> list[SetupNote]:
        return sorted(entries, key=lambda e: (e.date, e.added), reverse=True)  # newest first

    # --- reading
    def overview(self) -> SetupNotesOverview:
        return SetupNotesOverview(folder=str(self.md_dir), entries={k: self._sorted(v) for k, v in self._read().items()})

    def entries(self, setup_id: str) -> list[SetupNote]:
        return self._sorted(self._read().get(setup_id, []))

    def previous(self, setup_id: str) -> SetupSummary | None:
        """The version before this one of the same setup (same car, track and name), if it's still in the library."""
        all_ = self.library.all()
        me = next((s for s in all_ if s.id == setup_id), None)
        if me is None:
            return None
        older = [s for s in all_ if (s.car, s.track, s.name) == (me.car, me.track, me.name) and s.version < me.version]
        return max(older, key=lambda s: s.version, default=None)

    def changes(self, setup_id: str) -> list[SettingChange]:
        """Settings that differ from the previous version (none for a first version)."""
        prev = self.previous(setup_id)
        if prev is None:
            return []
        cmp = self.library.compare([prev.id, setup_id])
        return [SettingChange(label=r.label, group=r.group, before=r.values[0], after=r.values[1])
                for r in cmp.rows if r.changed]

    # --- editing (each one rewrites the Markdown)
    def add(self, setup_id: str, body: SetupNoteIn) -> SetupNote:
        entry = SetupNote(**body.model_dump() | {"date": _today_or(body.date)}, id=uuid.uuid4().hex[:8],
                          added=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        with _LOCK:
            notes = self._read()
            notes.setdefault(setup_id, []).append(entry)
            self._write(notes)
        self.write_markdown()
        return entry

    def edit(self, setup_id: str, entry_id: str, body: SetupNoteIn) -> SetupNote | None:
        with _LOCK:
            notes = self._read()
            for i, e in enumerate(notes.get(setup_id, [])):
                if e.id == entry_id:
                    notes[setup_id][i] = e.model_copy(update=body.model_dump() | {"date": _today_or(body.date)})
                    self._write(notes)
                    break
            else:
                return None
        self.write_markdown()
        return notes[setup_id][i]

    def delete(self, setup_id: str, entry_id: str) -> bool:
        with _LOCK:
            notes = self._read()
            kept = [e for e in notes.get(setup_id, []) if e.id != entry_id]
            if len(kept) == len(notes.get(setup_id, [])):
                return False
            notes[setup_id] = kept
            self._write(notes)
        self.write_markdown()
        return True

    def forget(self, setup_id: str) -> None:
        """The setup was deleted from the library: its log goes with it."""
        with _LOCK:
            notes = self._read()
            if notes.pop(setup_id, None) is not None:
                self._write(notes)
        self.write_markdown()

    # --- Excel: one row per entry, plus versions that only have a description
    def csv_text(self, setup_ids: list[str] | None = None) -> str:
        notes = self._read()
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(["Date", "Car", "Track", "Setup", "Version", "Author", "What changed", "Why", "Driver feedback",
                    "Setup description", "Setup id"])
        rows = []
        for s in self.library.all():
            if setup_ids and s.id not in setup_ids:
                continue
            base = [s.car, s.track, s.name, s.version]
            for e in notes.get(s.id, []):
                rows.append([e.date, *base, e.author, e.changed, e.why, e.feedback, s.notes, s.id])
            if not notes.get(s.id) and s.notes.strip():
                rows.append([s.saved_at[:10], *base, "", "", "", "", s.notes, s.id])
        rows.sort(key=lambda r: (r[0], r[1], r[3], r[4]), reverse=True)  # newest first
        w.writerows(rows)
        # Excel only reads accents and ° correctly from a CSV that starts with a byte-order mark
        return "﻿" + buf.getvalue()

    @staticmethod
    def _owned(path: Path) -> bool:
        if not path.is_file():
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
        return text.startswith("---\n") and f"generated: {_yaml(GENERATED)}" in text.split("---", 2)[1].splitlines()

    def _export_name(self, car: str, track: str, name: str) -> str:
        base = _file_name(car, track, name)
        digest = hashlib.sha256(json.dumps([car, track, name]).encode()).hexdigest()[:12]
        candidate = base
        for i in range(1000):
            path = self.md_dir / candidate
            if not path.exists():
                return candidate
            if self._owned(path):
                header = path.read_text(encoding="utf-8").split("---", 2)[1].splitlines()
                if all(f"{key}: {_yaml(value)}" in header for key, value in
                       (("title", name), ("car", car), ("track", track))):
                    return candidate
            candidate = f"{Path(base).stem} - {digest}" + (f"-{i}" if i else "") + ".md"
        raise ValueError("No free file name for the setup export")

    # --- Markdown, regenerated from the JSON after every change
    def write_markdown(self) -> None:
        with _LOCK:
            self._write_markdown()

    def _write_markdown(self) -> None:
        notes = self._read()
        families: dict[tuple, list[SetupSummary]] = {}
        for s in self.library.all():
            families.setdefault((s.car, s.track, s.name), []).append(s)
        # Only setups someone has written about get a file
        noted = {k: sorted(v, key=lambda s: -s.version) for k, v in families.items()
                 if any(notes.get(s.id) or s.notes.strip() for s in v)}
        self.md_dir.mkdir(parents=True, exist_ok=True)
        written = set()
        for (car, track, name), versions in noted.items():
            fname = self._export_name(car, track, name)
            self._save(self.md_dir / fname, self._family_markdown(car, track, name, versions, notes))
            written.add(fname)
        self._save(self.md_dir / INDEX_NAME, self._index_markdown(noted, notes))
        written.add(INDEX_NAME)
        # Files for setups that no longer have notes (or were deleted) - only ones this app wrote
        for f in self.md_dir.glob("*.md"):
            if f.name not in written and self._owned(f):
                f.unlink()

    @staticmethod
    def _save(path: Path, text: str) -> None:
        if path.exists() and not SetupNotes._owned(path):
            return  # an unrelated hand-written index must survive, too
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return  # unchanged: don't touch it (keeps file dates meaningful in Explorer / Obsidian)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)

    def _family_markdown(self, car: str, track: str, name: str, versions: list[SetupSummary],
                         notes: dict[str, list[SetupNote]]) -> str:
        dates = [e.date for s in versions for e in notes.get(s.id, [])] + [s.saved_at[:10] for s in versions]
        out = [
            "---",
            f"title: {_yaml(name)}",
            "type: setup",
            f"car: {_yaml(car)}",
            f"track: {_yaml(track)}",
            f"versions: {len(versions)}",
            f"updated: {max(dates)}",
            "tags: [setup]",
            f"generated: {_yaml(GENERATED)}",
            "---",
            f"# {name}",
            "",
            f"**Car:** {car} · **Track:** {track} · {len(versions)} version{'s' if len(versions) != 1 else ''}",
            "",
            "> Written by the FS Telemetry app from its setup library. Changes made in this file are",
            "> overwritten: add notes on the app's Setups page (Log). Newest version first.",
            "",
        ]
        for s in versions:
            out += [f"## v{s.version}", ""]
            when = f"saved {s.file_modified[:10]}" if s.file_modified else f"added {s.saved_at[:10]}"
            out += [f"*{when} · from {s.source}*", ""]
            if s.notes.strip():
                out += [s.notes.strip(), ""]
            changes = self.changes(s.id)
            if changes:
                out += ["**Changes from the version before** (setup-screen values):", ""]
                out += [f"- {c.label}: {_fmt(c.before)} → {_fmt(c.after)}" for c in changes] + [""]
            for e in self._sorted(notes.get(s.id, [])):
                out += [f"### {e.date}" + (f" · {e.author}" if e.author.strip() else ""), ""]
                for label, text in (("What changed", e.changed), ("Why", e.why), ("Driver feedback", e.feedback)):
                    if text.strip():
                        out.append(f"- **{label}:** {_quote(text)}")
                out.append("")
        return "\n".join(out)

    def _index_markdown(self, noted: dict[tuple, list[SetupSummary]], notes: dict[str, list[SetupNote]]) -> str:
        out = ["---", "title: Setup notes", "type: index", "tags: [setup]", f"generated: {_yaml(GENERATED)}", "---",
               "# Setup notes", "",
               "> Written by the FS Telemetry app. One file per setup; add notes on the app's Setups page.", ""]
        car = None
        for (c, track, name), versions in sorted(noted.items()):
            if c != car:
                car = c
                out += ["", f"## {car}", ""]
            count = sum(len(notes.get(s.id, [])) for s in versions)
            link = self._export_name(c, track, name).replace(" ", "%20")
            out.append(f"- [{name}]({link}) · {track} · v{versions[0].version} · {count} note{'s' if count != 1 else ''}")
        if not noted:
            out.append("No setup notes yet.")
        return "\n".join(out) + "\n"
