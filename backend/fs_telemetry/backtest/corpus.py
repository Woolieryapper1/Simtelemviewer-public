"""The runs a backtest checks the estimators on.

Two kinds:
  * real runs - your own telemetry files, found where the app finds them (config.py:
    FS_TELEMETRY_DIR for AC, FS_IRACING_DIR for iRacing). They are big and private, so
    they never go into git: the committed corpus file (backend/tests/backtest/corpus.json)
    only says which folders to use and how to group the runs. No files on this PC = no
    real runs, and the backtest just checks the synthetic ones.
  * synthetic runs - made up in code (synthetic.py) with a known answer, so every PC and
    every test run has something to check against.

The corpus file:
    {
      "folders": {"ac": "default", "iracing": "default"},   "default" = the app's own folder; or a path; or null = skip
      "exclude": ["*-old-*"],                              runs to leave out (file name patterns)
      "setups": {"24112025-2*": "baseline wing"}           AC files don't say which setup was used: label runs
    }                                                      here so "same car and setup" can be compared
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import config
from ..ingest.motec_ld import LdSession

DEFAULT_FOLDERS = {"ac": lambda: config.TELEMETRY_DIR, "iracing": lambda: config.IRACING_DIR}
PATTERNS = {"ac": "*.ld", "iracing": "*.ibt"}


@dataclass
class Run:
    """One session to test on. Adapters get one of these and call run.session() for the data."""

    id: str
    source: str  # "synthetic", "ac" or "iracing"
    car: str
    track: str = ""
    setup: str = "setup unknown"  # runs with the same car and setup should give the same estimates
    truth: dict[str, float] = field(default_factory=dict)  # known answers (synthetic runs)
    base: str | None = None  # for a perturbation: the run this one is a copy of, with one known change
    only_for: str | None = None  # a made-up run that only one estimator (by name) should be measured on
    path: Path | None = None
    load: Callable[[], LdSession] | None = None
    _session: LdSession | None = field(default=None, repr=False)

    @property
    def group(self) -> str:
        """What "the same car and setup" means for the stability checks."""
        return f"{self.car} / {self.setup}"

    def session(self) -> LdSession:
        """The session, read on first use (without maths channels - call
        maths.add_maths_channels on it if an estimator needs them)."""
        if self._session is None:
            self._session = self.load()
        return self._session

    def forget(self) -> None:
        """Drop the session from memory (real ones are several MB each)."""
        if self.path is not None:
            self._session = None


@dataclass
class Manifest:
    folders: dict[str, Path | None]
    exclude: list[str] = field(default_factory=list)
    setups: dict[str, str] = field(default_factory=dict)

    def setup_label(self, run_id: str) -> str | None:
        return next((label for pattern, label in self.setups.items() if fnmatch.fnmatch(run_id, pattern)), None)


def load_manifest(path: Path | None) -> Manifest:
    """Read the corpus file; with none (or a missing one), use the app's own folders."""
    raw = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}
    folders = {}
    for sim, default in DEFAULT_FOLDERS.items():
        where = raw.get("folders", {}).get(sim, "default")
        folders[sim] = default() if where == "default" else (Path(where) if where else None)
    return Manifest(folders=folders, exclude=raw.get("exclude", []), setups=raw.get("setups", {}))


def _ac_run(path: Path, folder: Path, manifest: Manifest) -> Run:
    from ..ingest.motec_ld import read_ld

    # telemetrick puts files in <driver>/<car>/<track>/; otherwise the car is read from the file when it's loaded
    parts = path.relative_to(folder).parts
    car, track = (parts[-3], parts[-2]) if len(parts) >= 3 else ("", "")
    return Run(id=path.stem, source="ac", car=car, track=track, path=path,
               setup=manifest.setup_label(path.stem) or "setup unknown", load=lambda: read_ld(path))


def _iracing_run(path: Path, folder: Path, manifest: Manifest) -> Run:
    from ..ingest.ibt import read_ibt, read_session_info, setup_values, who_where

    # every .ibt holds the setup it was driven with, so runs can be grouped by it automatically
    try:
        info, _ = read_session_info(path)
        _, car, track = who_where(info)
        setup = "setup " + hashlib.sha1(json.dumps(setup_values(info), sort_keys=True).encode()).hexdigest()[:6]
    except (OSError, ValueError, IndexError, KeyError):
        car, track, setup = "", "", "setup unknown"
    return Run(id=path.stem, source="iracing", car=car, track=track, path=path,
               setup=manifest.setup_label(path.stem) or setup, load=lambda: read_ibt(path))


def real_runs(manifest: Manifest, sims: set[str]) -> list[Run]:
    """Every telemetry file in the corpus folders, for the sims asked for. Missing folders are skipped."""
    makers = {"ac": _ac_run, "iracing": _iracing_run}
    runs = []
    for sim in sorted(sims & set(makers)):
        folder = manifest.folders.get(sim)
        if not folder or not folder.exists():
            continue
        for path in sorted(folder.rglob(PATTERNS[sim])):
            if not any(fnmatch.fnmatch(path.stem, pattern) for pattern in manifest.exclude):
                runs.append(makers[sim](path, folder, manifest))
    return runs


def fingerprint(runs: list[Run]) -> str:
    """A short id for "this exact set of runs", so a baseline made on other files is noticed."""
    return hashlib.sha1("\n".join(sorted(r.id for r in runs)).encode()).hexdigest()[:10]
