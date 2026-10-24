"""Finds telemetry files on disk and keeps parsed sessions in memory.

Three kinds of file:
  * .ld   - MoTeC files from AC: the telemetrick app or CSP's own logger (ingest/motec_ld.py,
             ingest/csp.py), in TELEMETRY_DIR
  * .ibt  - iRacing's own telemetry files (ingest/ibt.py), in IRACING_DIR
  * .csv  - CSV from anything else: other sims, loggers, the app's own downloads (ingest/csv_import.py), in CSV_DIR
plus the same kinds of file that teammates shared in the team library (shared.py).
All the readers give back the same kind of session, so nothing after this point
needs to know where a file came from (it's in session.source if it does).

Two caches, because sessions are big (~4 MB each in memory for AC, ~15 MB for
iRacing) but their summaries are tiny:
  * summaries: one per file, kept forever (until the file changes)
  * full sessions: only the few most recently used are kept
"""

from __future__ import annotations

import hashlib
import logging
import struct
from collections import OrderedDict
from dataclasses import dataclass, fields, replace
from datetime import datetime
from pathlib import Path

from .config import CSP_DIR, CSV_DIR, IRACING_DIR, TELEMETRY_DIR
from .ingest.csv_import import read_csv
from .ingest.ibt import read_ibt
from .ingest.motec_ld import LdSession, read_ld
from .laps import best_lap, lap_infos, track_length
from .maths import add_maths_channels
from .shared import SharedLibrary, long_path

MAX_SESSIONS_IN_MEMORY = 8
READERS = {".ld": read_ld, ".ibt": read_ibt, ".csv": read_csv}
log = logging.getLogger(__name__)


@dataclass
class SessionSummary:
    id: str  # the file name without .ld/.ibt/.csv; "<person>~<file name>" for a run only in the team library
    key: str  # stays the same when the run is exported again or renamed (see session_key): notes are filed under it
    source: str  # "ac", "iracing" or "csv"
    driver: str
    car: str
    track: str
    started: datetime | None
    duration_s: float
    track_length_m: float
    lap_count: int
    valid_laps: int  # full timed laps that weren't invalidated
    best_lap_s: float | None
    best_lap_number: int | None
    owner: str = ""  # whose run it is: its folder in the team library, or my name for a run only on this PC
    mine: bool = True
    shared: bool = False  # in the team library


def read_session(path: Path) -> LdSession:
    """Read a file of either kind, without maths channels."""
    return READERS[path.suffix.lower()](long_path(path))


def started_at(s: LdSession) -> datetime | None:
    """When the run started, from the file's own details."""
    try:
        return datetime.strptime(f"{s.date} {s.time}", "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return None


def session_key(s: LdSession) -> str:
    """A name for the run that doesn't depend on its file.

    The file name changes if the run is exported again, renamed or moved, but who
    drove, in what, where, and when they left the pits doesn't - so hash those.
    (Checked on 275 real runs: no two share a key.) A file with no start time
    falls back to its file name."""
    started = started_at(s)
    if started is None:
        return f"file-{s.path.stem}"
    text = "|".join([s.source, s.driver, s.car, s.track, started.isoformat()])
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


class Catalog:
    def __init__(self, root: Path = TELEMETRY_DIR, iracing_root: Path | None = IRACING_DIR,
                 csv_root: Path | None = CSV_DIR, csp_root: Path | None = CSP_DIR,
                 shared: SharedLibrary | None = None):
        self.root = root
        self.csp_root = csp_root
        self.iracing_root = iracing_root
        self.csv_root = csv_root
        self.shared = shared or SharedLibrary()
        self._summaries: dict[Path, tuple[float, SessionSummary]] = {}
        self._sessions: OrderedDict[Path, tuple[float, LdSession]] = OrderedDict()
        self._unreadable: dict[Path, float] = {}  # files that failed to read (by mtime), so we don't retry every time

    def _paths(self) -> dict[str, tuple[Path, str | None]]:
        """Every run by id: (its file, whose folder of the team library it's in - None if it's on this PC)."""
        found = [(self.root, "*.ld"), (self.csp_root, "*.ld"), (self.iracing_root, "*.ibt"), (self.csv_root, "*.csv")]
        paths = {p.stem: (p, None) for root, pattern in found if root and root.exists() for p in root.rglob(pattern)}
        paths.update({f"{owner}~{p.stem}": (p, owner) for owner, p in self.shared.runs()})
        return paths

    def summaries(self) -> list[SessionSummary]:
        """Every run, newest first. A run that's both on this PC and in the team
        library (shared from here, or a teammate's copied over by hand) is listed
        once, as the copy on this PC."""
        local, team = [], {}
        for session_id, (path, owner) in self._paths().items():
            s = self._summary(path) if owner is None else self._team_summary(session_id, path, owner)
            if s is None:
                continue
            if owner is None:
                local.append(s)
            else:
                team.setdefault(s.key, s)
        out = []
        for s in local:
            t = team.pop(s.key, None)
            out.append(replace(s, owner=t.owner, mine=t.mine, shared=True) if t else replace(s, owner=self.shared.me))
        out += team.values()
        return sorted(out, key=lambda s: s.started or datetime.min, reverse=True)

    def _summary(self, path: Path) -> SessionSummary | None:
        """A run's summary, from the run itself: read once, until the file changes."""
        mtime = long_path(path).stat().st_mtime
        cached = self._summaries.get(path)
        if cached is None or cached[0] != mtime:
            if self._unreadable.get(path) == mtime:
                return None
            try:
                # summaries only need laps, so skip the maths channels (much quicker over hundreds of files)
                cached = (mtime, self._summarise(path, read_session(path)))
            except (ValueError, KeyError, IndexError, struct.error, OSError) as e:
                log.warning("Skipping %s: can't read it (%s)", path.name, e)
                self._unreadable[path] = mtime
                return None
            self._summaries[path] = cached
        return cached[1]

    def _team_summary(self, session_id: str, path: Path, owner: str) -> SessionSummary | None:
        """A run in the team library: from the summary saved next to it if there is
        one, so OneDrive doesn't download the run just to list it."""
        s = _from_saved(self.shared.read_summary(path)) or self._summary(path)
        if s is None:
            return None
        return replace(s, id=session_id, owner=owner, mine=owner == self.shared.me, shared=True)

    def summary(self, session_id: str) -> SessionSummary | None:
        return next((s for s in self.summaries() if s.id == session_id), None)

    def get(self, session_id: str) -> LdSession | None:
        found = self._paths().get(session_id)
        return self._load(found[0]) if found else None

    def share(self, session_id: str) -> SessionSummary | None:
        """Put one of my runs on this PC in the team library. None if there's no such run."""
        found, s = self._paths().get(session_id), self.summary(session_id)
        if found is None or s is None:
            return None
        if not self.shared.enabled:
            raise ValueError("No team library folder is set (FS_SHARED_DIR in my-settings.bat)")
        if found[1] is not None or not s.mine:
            raise ValueError("Only your own runs on this PC can be shared")
        if not s.shared:
            self.shared.share(found[0], replace(s, owner="", mine=True, shared=False))
        return self.summary(session_id)

    def unshare(self, session_id: str) -> SessionSummary | None:
        """Take one of my runs out of the team library (a copy on this PC stays)."""
        s = self.summary(session_id)
        if s is None:
            return None
        if not (s.shared and s.mine):
            raise ValueError("Only your own shared runs can be taken out of the team library")
        for team_id, (path, owner) in self._paths().items():
            if owner == self.shared.me and (t := self._team_summary(team_id, path, owner)) and t.key == s.key:
                self.shared.unshare(path)
        return self.summary(session_id) or replace(s, shared=False)

    def _load(self, path: Path) -> LdSession:
        mtime = long_path(path).stat().st_mtime
        cached = self._sessions.get(path)
        if cached is None or cached[0] != mtime:
            cached = (mtime, add_maths_channels(read_session(path)))  # maths channels added once, on load
        self._sessions[path] = cached
        self._sessions.move_to_end(path)
        while len(self._sessions) > MAX_SESSIONS_IN_MEMORY:
            self._sessions.popitem(last=False)
        return cached[1]

    @staticmethod
    def _summarise(path: Path, s: LdSession) -> SessionSummary:
        started = started_at(s)
        infos = lap_infos(s)
        best = best_lap(infos)
        return SessionSummary(
            id=path.stem,
            key=session_key(s),
            source=s.source,
            driver=s.driver,
            car=s.car,
            track=s.track,
            started=started,
            duration_s=round(s.duration, 3),
            track_length_m=round(track_length(s), 1),
            lap_count=len(infos),
            valid_laps=sum(1 for l in infos if l.kind == "timed" and not l.invalid),
            best_lap_s=round(best.lap_time, 3) if best else None,
            best_lap_number=best.number if best else None,
        )


def _from_saved(data: dict | None) -> SessionSummary | None:
    """A summary saved next to a run in the team library (by any version of the
    app: fields it doesn't know are ignored; if one it needs is missing, None, and
    the run is read instead)."""
    if not data:
        return None
    known = {f.name for f in fields(SessionSummary)}
    try:
        data = {k: v for k, v in data.items() if k in known}
        data["started"] = datetime.fromisoformat(data["started"]) if data.get("started") else None
        return SessionSummary(**data)
    except (TypeError, ValueError):
        return None
