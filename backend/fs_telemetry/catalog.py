"""Finds telemetry files on disk and keeps parsed sessions in memory.

Three kinds of file:
  * .ld   - MoTeC files from AC: the telemetrick app or CSP's own logger (ingest/motec_ld.py,
             ingest/csp.py), in TELEMETRY_DIR
  * .ibt  - iRacing's own telemetry files (ingest/ibt.py), in IRACING_DIR
  * .csv  - CSV from anything else: other sims, loggers, the app's own downloads (ingest/csv_import.py), in CSV_DIR
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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import CSP_DIR, CSV_DIR, IRACING_DIR, TELEMETRY_DIR
from .ingest.csv_import import read_csv
from .ingest.ibt import read_ibt
from .ingest.motec_ld import LdSession, read_ld
from .laps import best_lap, lap_infos, track_length
from .maths import add_maths_channels

MAX_SESSIONS_IN_MEMORY = 8
READERS = {".ld": read_ld, ".ibt": read_ibt, ".csv": read_csv}
log = logging.getLogger(__name__)


@dataclass
class SessionSummary:
    id: str  # the file name without .ld/.ibt/.csv - readable and unique per export
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


def read_session(path: Path) -> LdSession:
    """Read a file of either kind, without maths channels."""
    return READERS[path.suffix.lower()](path)


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
                 csv_root: Path | None = CSV_DIR, csp_root: Path | None = CSP_DIR):
        self.root = root
        self.csp_root = csp_root
        self.iracing_root = iracing_root
        self.csv_root = csv_root
        self._summaries: dict[Path, tuple[float, SessionSummary]] = {}
        self._sessions: OrderedDict[Path, tuple[float, LdSession]] = OrderedDict()
        self._unreadable: dict[Path, float] = {}  # files that failed to read (by mtime), so we don't retry every time

    def _paths(self) -> dict[str, Path]:
        found = [(self.root, "*.ld"), (self.csp_root, "*.ld"), (self.iracing_root, "*.ibt"), (self.csv_root, "*.csv")]
        return {p.stem: p for root, pattern in found if root and root.exists() for p in root.rglob(pattern)}

    def summaries(self) -> list[SessionSummary]:
        out = []
        for path in self._paths().values():
            mtime = path.stat().st_mtime
            cached = self._summaries.get(path)
            if cached is None or cached[0] != mtime:
                if self._unreadable.get(path) == mtime:
                    continue
                try:
                    # summaries only need laps, so skip the maths channels (much quicker over hundreds of files)
                    cached = (mtime, self._summarise(path, read_session(path)))
                except (ValueError, KeyError, IndexError, struct.error, OSError) as e:
                    log.warning("Skipping %s: can't read it (%s)", path.name, e)
                    self._unreadable[path] = mtime
                    continue
                self._summaries[path] = cached
            out.append(cached[1])
        return sorted(out, key=lambda s: s.started or datetime.min, reverse=True)

    def summary(self, session_id: str) -> SessionSummary | None:
        return next((s for s in self.summaries() if s.id == session_id), None)

    def get(self, session_id: str) -> LdSession | None:
        path = self._paths().get(session_id)
        return self._load(path) if path else None

    def _load(self, path: Path) -> LdSession:
        mtime = path.stat().st_mtime
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
