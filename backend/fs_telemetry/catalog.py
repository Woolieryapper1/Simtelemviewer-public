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
  * summaries: one per file, kept forever (until the file changes), and saved in
    CACHE_FILE so the run list doesn't read every run again each time the app starts
    (~18 s for 300 runs, mostly CSP and iRacing files)
  * full sessions: only the few most recently used are kept
"""

from __future__ import annotations

import hashlib
import json
import logging
import struct
import threading
from collections import OrderedDict
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from pathlib import Path

from . import config
from .config import CSP_DIR, CSV_DIR, IRACING_DIR, TELEMETRY_DIR, TRACKS_DIRS
from .ingest import csv_import
from .ingest.ac_track import canonical_track, track_aliases
from .ingest.csv_import import read_csv
from .ingest.ibt import read_ibt
from .ingest.motec_ld import LdSession, read_ld
from .laps import best_lap, lap_infos, track_length
from .maths import add_maths_channels
from .shared import SharedLibrary, long_path, write_atomic

MAX_SESSIONS_IN_MEMORY = 8
READERS = {".ld": read_ld, ".ibt": read_ibt, ".csv": read_csv}
CACHE_FILE = config.DATA_DIR / "run_summaries.json"
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
    falls back to its file name. The track is the name the file has, from before
    track names were tidied up (ac_track.canonical_track), so keys never change."""
    started = started_at(s)
    if started is None:
        return f"file-{s.path.stem}"
    text = "|".join([s.source, s.driver, s.car, s.logged_track or s.track, started.isoformat()])
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def summaries_version() -> str:
    """What a run's summary depends on besides the run's own file: the code that
    reads runs and splits them into laps, the installed tracks (a CSP run's length
    comes from the track's AI line) and the track aliases. The saved summaries are
    only used while this stays the same; when it changes (a new version of the app,
    a track installed) every run is read again, once."""
    here = Path(__file__).parent
    h = hashlib.sha1()
    for f in sorted([here / "catalog.py", here / "laps.py", *(here / "ingest").glob("*.py")]):
        h.update(f.read_bytes())
    for root in TRACKS_DIRS:
        if root.is_dir():
            h.update("|".join(sorted(p.name for p in root.iterdir())).encode("utf-8"))
    h.update(json.dumps(track_aliases(), sort_keys=True).encode("utf-8"))
    return h.hexdigest()[:16]


def file_stamp(path: Path) -> tuple[int, ...]:
    """What tells a run's summary is out of date: the file's modified time and size,
    and for a CSV when the column mappings last changed (they decide how it's read)."""
    st = long_path(path).stat()
    if path.suffix.lower() != ".csv":
        return st.st_mtime_ns, st.st_size
    try:
        mappings = csv_import.MAPPINGS_FILE.stat().st_mtime_ns
    except OSError:
        mappings = 0
    return st.st_mtime_ns, st.st_size, mappings


class Catalog:
    def __init__(self, root: Path = TELEMETRY_DIR, iracing_root: Path | None = IRACING_DIR,
                 csv_root: Path | None = CSV_DIR, csp_root: Path | None = CSP_DIR,
                 shared: SharedLibrary | None = None, cache_file: Path | None = None):
        self.root = root
        self.csp_root = csp_root
        self.iracing_root = iracing_root
        self.csv_root = csv_root
        self.shared = shared or SharedLibrary()
        self._cache_file = cache_file  # None: CACHE_FILE, looked up when it's first read (tests point it elsewhere)
        # By file: (its file_stamp, its summary - None if it can't be read, so it isn't tried every time)
        self._summaries: dict[Path, tuple[tuple[int, ...], SessionSummary | None]] = {}
        self._sessions: OrderedDict[Path, tuple[float, LdSession]] = OrderedDict()
        self._cache_loaded = False
        self._cache_changed = False
        self._lock = threading.RLock()  # one listing at a time: the first one after a start reads every new run

    @property
    def cache_file(self) -> Path:
        return self._cache_file or CACHE_FILE

    def _load_cache(self) -> None:
        """The summaries saved by the last run of the app, if they're still good."""
        self._cache_loaded = True
        self._version = summaries_version()
        try:
            data = json.loads(long_path(self.cache_file).read_text(encoding="utf-8"))
            if data.get("version") != self._version:
                return
            for path, (stamp, summary) in data["runs"].items():
                s = _from_dict(summary) if summary is not None else None
                if s is not None or summary is None:
                    self._summaries[Path(path)] = (tuple(stamp), s)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            if not isinstance(e, FileNotFoundError):
                log.warning("Reading every run again: can't use the saved summaries in %s (%s)", self.cache_file, e)

    def _save_cache(self, paths: set[Path]) -> None:
        """Save the summaries of the runs that are still there, if anything changed."""
        gone = self._summaries.keys() - paths
        for path in gone:
            del self._summaries[path]
        if not (gone or self._cache_changed):
            return
        runs = {str(p): [stamp, _to_dict(s) if s else None] for p, (stamp, s) in self._summaries.items()}
        try:
            long_path(self.cache_file.parent).mkdir(parents=True, exist_ok=True)
            write_atomic(long_path(self.cache_file), json.dumps({"version": self._version, "runs": runs}))
            self._cache_changed = False
        except OSError as e:
            log.warning("Can't save the run summaries in %s (%s)", self.cache_file, e)

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
        with self._lock:
            if not self._cache_loaded:
                self._load_cache()
            paths = self._paths()
            for session_id, (path, owner) in paths.items():
                s = self._summary(path) if owner is None else self._team_summary(session_id, path, owner)
                if s is None:
                    continue
                if owner is None:
                    local.append(s)
                else:
                    team.setdefault(s.key, s)
            self._save_cache({path for path, _ in paths.values()})
        out = []
        for s in local:
            t = team.pop(s.key, None)
            out.append(replace(s, owner=t.owner, mine=t.mine, shared=True) if t else replace(s, owner=self.shared.me))
        out += team.values()
        return sorted(out, key=lambda s: s.started or datetime.min, reverse=True)

    def _summary(self, path: Path) -> SessionSummary | None:
        """A run's summary, from the run itself: read once, until the file changes."""
        stamp = file_stamp(path)
        cached = self._summaries.get(path)
        if cached is None or cached[0] != stamp:
            try:
                # summaries only need laps, so skip the maths channels (much quicker over hundreds of files)
                summary = self._summarise(path, read_session(path))
                # Reading a CSV of a new kind saves its column mapping: the file's own part
                # of the stamp is from before reading it, the mappings' from after
                cached = (stamp[:2] + file_stamp(path)[2:], summary)
            except (ValueError, KeyError, IndexError, struct.error, OSError) as e:
                log.warning("Skipping %s: can't read it (%s)", path.name, e)
                cached = (stamp, None)
            self._summaries[path] = cached
            self._cache_changed = True
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


def _to_dict(s: SessionSummary) -> dict:
    data = asdict(s)
    data["started"] = s.started.isoformat() if s.started else None
    return data


def _from_dict(data: dict | None) -> SessionSummary | None:
    """A summary as _to_dict saved it (by any version of the app: fields it doesn't
    know are ignored; if one it needs is missing, None)."""
    if not data:
        return None
    known = {f.name for f in fields(SessionSummary)}
    try:
        data = {k: v for k, v in data.items() if k in known}
        data["started"] = datetime.fromisoformat(data["started"]) if data.get("started") else None
        return SessionSummary(**data)
    except (TypeError, ValueError, AttributeError):
        return None


def _from_saved(data: dict | None) -> SessionSummary | None:
    """A summary saved next to a run in the team library (None: the run is read
    instead). Its track name is tidied up as if the run were read here, as an older
    app may have saved it as the file had it."""
    s = _from_dict(data)
    if s is None or s.source != "ac" or not isinstance(s.track, str):
        return s
    return replace(s, track=canonical_track(s.track, TRACKS_DIRS, track_aliases()))
