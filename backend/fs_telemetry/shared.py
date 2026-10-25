"""The team library: a folder everyone on the team syncs (a folder in the SUFST
Teams/SharePoint site, synced to each PC by OneDrive), where people share runs
so the others see them without sending files around.

    <FS_SHARED_DIR>/
        <name>/runs/<run>.ld                  one folder per person
        <name>/runs/<run>.ldx                 (an AC run's lap beacons go with it)
        <name>/runs/<run>.ld.summary.json     what the run list shows

Only the person a folder is named after writes in it, so OneDrive never has two
edits of one file to merge (it would keep both as "conflict copies").

The summary next to each run is there because OneDrive keeps files nobody has
opened yet as online-only placeholders. Listing the library reads the summaries,
a few KB each, instead of downloading every run; a run is only downloaded when
someone opens it. A run with no summary (copied in by hand) is still listed, by
reading the run itself.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

from .config import SHARED_DIR, SHARED_NAME

PATTERNS = ("*.ld", "*.ibt", "*.csv")
COMPANIONS = (".ldx",)  # files that go with a run of the same name
SUMMARY_SUFFIX = ".summary.json"


def folder_name(name: str) -> str:
    """A person's name as a folder name: no characters Windows won't allow in one,
    and no ~ (it separates the person from the file in a run's id)."""
    return re.sub(r'[<>:"/\\|?*~\x00-\x1f]', "_", name).strip(" .") or "unknown"


def summary_path(run: Path) -> Path:
    return run.with_name(run.name + SUMMARY_SUFFIX)


def long_path(path: Path) -> Path:
    """A path Windows can open even if it's over its usual 260-character limit
    (synced Teams folders are deep, and AC/CSP file names are long): the \\\\?\\ form
    of it. Shorter paths, and paths elsewhere, are left as they are."""
    if os.name != "nt" or len(str(path)) < 240 or str(path).startswith("\\\\"):
        return path
    return Path("\\\\?\\" + str(path.absolute()))


class SharedLibrary:
    def __init__(self, root: Path | None = SHARED_DIR, me: str = SHARED_NAME):
        self.root = root
        self.me = folder_name(me)

    @property
    def enabled(self) -> bool:
        return self.root is not None and self.root.is_dir()

    @property
    def my_runs(self) -> Path:
        return self.root / self.me / "runs"

    def people(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if (p / "runs").is_dir()) if self.enabled else []

    def runs(self) -> Iterator[tuple[str, Path]]:
        """(whose run, the run's file) for every run in the library."""
        for person in self.people():
            runs = self.root / person / "runs"
            for pattern in PATTERNS:
                for path in runs.rglob(pattern):
                    yield person, path

    @staticmethod
    def read_summary(run: Path) -> dict | None:
        """The summary saved next to a run, or None if there isn't one (or it can't be read)."""
        try:
            return json.loads(long_path(summary_path(run)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def share(self, run: Path, summary) -> Path:
        """Copy a run (and its companions) into my folder, then its summary. The
        summary goes last, so it never describes a run that isn't all there.
        Returns the copy."""
        long_path(self.my_runs).mkdir(parents=True, exist_ok=True)
        target = self.my_runs / run.name
        for f in [run, *(run.with_suffix(s) for s in COMPANIONS)]:
            if long_path(f).exists():
                _copy(long_path(f), long_path(self.my_runs / f.name))
        data = asdict(summary)
        data["started"] = summary.started.isoformat() if summary.started else None
        write_atomic(long_path(summary_path(target)), json.dumps(data, indent=1))
        return target

    def unshare(self, run: Path) -> None:
        """Take one of my runs out of the library (it goes to OneDrive's recycle bin)."""
        if run.parent.resolve() != self.my_runs.resolve():
            raise ValueError(f"'{run.name}' isn't in your folder of the team library")
        for f in [summary_path(run), run, *(run.with_suffix(s) for s in COMPANIONS)]:
            long_path(f).unlink(missing_ok=True)


def _copy(src: Path, dst: Path) -> None:
    """Copy under a temporary name, then rename, so a half-copied run is never seen."""
    fd, tmp = tempfile.mkstemp(dir=dst.parent, suffix=".tmp")
    os.close(fd)
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_atomic(dst: Path, text: str) -> None:
    """Write under a temporary name, then rename, so a half-written file is never seen."""
    fd, tmp = tempfile.mkstemp(dir=dst.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, dst)
