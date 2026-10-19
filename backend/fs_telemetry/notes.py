"""Session notes: what the team writes about each run, kept in the data folder.

For every run you can keep
  * notes ("new rear wing, cold tyres")
  * tags ("baseline", "race sim"...) and a star for favourites
  * per lap: a note ("flat spot") and a flag - "use" or "ignore" - that the Stint
    averages follow (see classify_laps in stint.py)

Everything goes in one JSON file, session-notes.json in the FS Telemetry folder,
written atomically like the layouts and the setup library.

Runs are filed under a *stable key* rather than their file name (see session_key
in catalog.py): a hash of the sim, driver, car, track and the time the run started,
which come from inside the file. Exporting the same run again, renaming the file or
moving it to another folder gives the same key, so the notes follow the run.

The file can be exported and imported to share notes between PCs. Importing merges
run by run: a run only on one side is kept, and for a run on both the copy that was
edited last wins (merging two sets of text line by line would duplicate it every
time the same file is imported again).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .config import DATA_DIR

FILE_FORMAT = "fs-telemetry session notes"
# Offered as suggestions next to the tags already in use
SUGGESTED_TAGS = ["baseline", "race sim", "quali", "wet", "new setup"]
MAX_TAGS = 20

LapFlag = Literal["use", "ignore"]


class LapNote(BaseModel):
    note: str = ""
    flag: LapFlag | None = None  # None: let the Laps counted filter decide


class SessionNote(BaseModel):
    key: str  # the stable key the note is filed under
    # Copied from the run each time it's edited, so the file makes sense to someone
    # reading it, and the export says which run each note is about
    session_id: str = ""  # file name, at the last edit
    source: str = ""
    driver: str = ""
    car: str = ""
    track: str = ""
    started: str | None = None
    notes: str = ""
    tags: list[str] = []
    star: bool = False
    laps: dict[int, LapNote] = {}  # by lap number
    updated: str = ""  # ISO time of the last edit (decides which copy wins on import)


class SessionNoteIn(BaseModel):
    """An edit from the dashboard. Only the fields sent are changed."""

    notes: str | None = Field(None, max_length=4000)
    tags: list[str] | None = Field(None, max_length=MAX_TAGS)
    star: bool | None = None


class LapNoteIn(BaseModel):
    """An edit to one lap. Only the fields sent are changed; flag null clears the flag."""

    note: str | None = Field(None, max_length=1000)
    flag: LapFlag | None = None


class ImportResult(BaseModel):
    added: int  # runs that had no notes here
    updated: int  # runs whose notes were replaced by a newer copy
    kept: int  # runs where the notes here were newer (or the same)


def clean_tags(tags: list[str]) -> list[str]:
    """Tags are compared without case or extra spaces: ' Race  Sim' and 'race sim' are one tag."""
    out = []
    for t in tags:
        t = re.sub(r"\s+", " ", t).strip().lower()[:30]
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _is_empty(n: SessionNote) -> bool:
    return not (n.notes.strip() or n.tags or n.star or n.laps)


class NotesStore:
    def __init__(self, path: Path | None = None):
        self.path = path or DATA_DIR / "session-notes.json"
        # The API answers requests on several threads at once: two quick edits (a lap's note,
        # then its flag) must not both read the file before either writes it, or one is lost
        self._lock = threading.Lock()

    # --- the file
    def _read(self) -> dict[str, SessionNote]:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return {k: SessionNote(**v) for k, v in data.get("sessions", {}).items()}

    def _write(self, notes: dict[str, SessionNote]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._file(notes), f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)

    @staticmethod
    def _file(notes: dict[str, SessionNote]) -> dict:
        return {"format": FILE_FORMAT, "version": 1,
                "sessions": {k: n.model_dump() for k, n in sorted(notes.items())}}

    # --- reading
    def all(self) -> dict[str, SessionNote]:
        return {key: note for key, note in self._read().items() if not _is_empty(note)}

    def get(self, key: str) -> SessionNote | None:
        return self._read().get(key)

    def flags(self, key: str) -> dict[int, str]:
        """{lap number: "use" / "ignore"} for the laps of a run that have a flag."""
        n = self.get(key)
        return {lap: ln.flag for lap, ln in n.laps.items() if ln.flag} if n else {}

    def tags(self) -> list[str]:
        """Tags in use (most used first), then the suggested ones not used yet."""
        counts: dict[str, int] = {}
        for n in self._read().values():
            for t in n.tags:
                counts[t] = counts.get(t, 0) + 1
        used = sorted(counts, key=lambda t: (-counts[t], t))
        return used + [t for t in SUGGESTED_TAGS if t not in counts]

    # --- editing. `label` describes the run (session_id, source, driver, car, track, started)
    def _edit(self, key: str, label: dict, change) -> SessionNote:
        with self._lock:
            return self._edit_locked(key, label, change)

    def _edit_locked(self, key: str, label: dict, change) -> SessionNote:
        notes = self._read()
        n = notes.get(key) or SessionNote(key=key)
        n = n.model_copy(update={**label, "key": key})
        change(n)
        n.updated = _now()
        # Empty records remember deletions when an older notes file is imported.
        notes[key] = n
        self._write(notes)
        return n

    def update(self, key: str, label: dict, edit: SessionNoteIn) -> SessionNote:
        def change(n: SessionNote):
            sent = edit.model_fields_set
            if "notes" in sent and edit.notes is not None:
                n.notes = edit.notes
            if "tags" in sent and edit.tags is not None:
                n.tags = clean_tags(edit.tags)
            if "star" in sent and edit.star is not None:
                n.star = edit.star
        return self._edit(key, label, change)

    def update_lap(self, key: str, label: dict, lap: int, edit: LapNoteIn) -> SessionNote:
        def change(n: SessionNote):
            ln = n.laps.get(lap, LapNote()).model_copy()
            sent = edit.model_fields_set
            if "note" in sent and edit.note is not None:
                ln.note = edit.note
            if "flag" in sent:
                ln.flag = edit.flag  # None clears it
            laps = {**n.laps, lap: ln}
            if not ln.note.strip() and not ln.flag:
                laps.pop(lap)
            n.laps = dict(sorted(laps.items()))
        return self._edit(key, label, change)

    # --- sharing between PCs
    def export(self) -> dict:
        return self._file(self._read())

    def import_file(self, data: dict) -> ImportResult:
        if not isinstance(data, dict) or data.get("format") != FILE_FORMAT:
            raise ValueError("That isn't a session notes file exported from this app")
        try:
            incoming = {k: SessionNote(**{**v, "key": k}) for k, v in data.get("sessions", {}).items()}
        except (ValidationError, TypeError, AttributeError) as e:
            raise ValueError(f"The notes file is damaged: {e}") from e
        with self._lock:
            return self._merge(incoming)

    def _merge(self, incoming: dict[str, SessionNote]) -> ImportResult:
        notes = self._read()
        result = ImportResult(added=0, updated=0, kept=0)
        for key, theirs in incoming.items():
            theirs.tags = clean_tags(theirs.tags)
            ours = notes.get(key)
            if ours is None:
                notes[key] = theirs
                result.added += 1
            elif theirs.updated > ours.updated:  # ISO times sort as text
                notes[key] = theirs
                result.updated += 1
            else:
                result.kept += 1
        self._write(notes)
        return result
