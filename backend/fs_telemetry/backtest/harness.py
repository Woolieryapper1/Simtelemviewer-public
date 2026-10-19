"""Runs every estimator over the corpus, and checks the result against the baseline.

An ADAPTER plugs one estimator into the harness. It is just:
    name       short id, used in the report and in the baseline file (e.g. "aero_fit")
    title      a plain-English heading for the report
    sources    which runs it can be checked on: "synthetic", "ac", "iracing"
    measure    function(run) -> {number name: value} for one run, or None if it can't estimate on that run
    summarise  function(list of Measured) -> {metric: value} for a whole corpus. These are what get gated.
    checks     which of those metrics are gated, which way is better, and how much worse than
               the baseline is still accepted

The runner calls measure on every run (each source separately: synthetic, AC,
iRacing), then summarise, then compares the summary with the committed baseline:
a metric worse than its baseline by more than the tolerance, or past its hard
limit, FAILS the gate.
"""

from __future__ import annotations

import hashlib
import json
import math
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .corpus import Run, fingerprint

SOURCES = ("synthetic", "ac", "iracing")


@dataclass
class Check:
    metric: str
    better: str  # "lower" (errors, spreads) or "higher" (R², rank agreement)
    tolerance: float  # how much worse than the baseline is still a pass, in the metric's own units
    limit: float | None = None  # a hard line it must never cross, baseline or not

    def worse(self, value: float, than: float) -> float:
        """How much worse value is than `than` (negative = better)."""
        return value - than if self.better == "lower" else than - value


@dataclass
class Measured:
    run: Run
    values: dict[str, float] | None  # None = the estimator couldn't run on this one
    error: str = ""  # the estimator crashed: what it said


def column(measured: list[Measured], key: str) -> np.ndarray:
    """One number from every run (NaN where a run doesn't have it), for summarise functions."""
    return np.array([m.values.get(key, np.nan) if m.values else np.nan for m in measured], float)


def perturbation_pairs(measured: list[Measured]) -> list[tuple[Measured, Measured]]:
    """(base run, changed run) for every run that is a copy of another with one known change."""
    by_id = {m.run.id: m for m in measured}
    return [(by_id[m.run.base], m) for m in measured
            if m.run.base in by_id and m.values and by_id[m.run.base].values]


@dataclass
class Adapter:
    name: str
    title: str
    sources: tuple[str, ...]
    measure: Callable[[Run], dict[str, float] | None]
    summarise: Callable[[list[Measured]], dict[str, float]]
    checks: list[Check]
    notes: str = ""  # shown under the title in the report: what is checked and how
    synthetic: Callable[[], list[Run]] | None = None  # made-up runs of its own (marked only_for=its name)


@dataclass
class Verdict:
    metric: str
    value: float | None
    baseline: float | None
    status: str  # "pass", "FAIL", "better" (than baseline by more than the tolerance), "new", "not gated"
    why: str = ""


@dataclass
class SourceResult:
    adapter: Adapter
    source: str
    measured: list[Measured]
    summary: dict[str, float]
    verdicts: list[Verdict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # e.g. "the corpus differs from the baseline's"

    @property
    def failed(self) -> bool:
        return any(v.status == "FAIL" for v in self.verdicts)


# --- Running ------------------------------------------------------------------------


def code_version() -> str:
    """A hash of the app's Python code: any change to an estimator throws the cache away."""
    root = Path(__file__).resolve().parents[1]
    h = hashlib.sha1()
    for f in sorted(root.rglob("*.py")):
        h.update(f.read_bytes())
    return h.hexdigest()[:12]


class Cache:
    """Per-run results, kept between runs of the backtest for real files (they're the slow part).
    Keyed by adapter + file + its size and date, and thrown away whenever the code changes."""

    def __init__(self, path: Path | None):
        self.path = path
        self.version = code_version() if path else ""
        self.items: dict[str, dict] = {}
        if path and path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved.get("code") == self.version:
                    self.items = saved.get("items", {})
            except (ValueError, OSError):
                pass

    @staticmethod
    def key(adapter: Adapter, run: Run) -> str | None:
        if run.path is None:
            return None  # synthetic runs are made fresh each time
        st = run.path.stat()
        return f"{adapter.name}|{run.path}|{st.st_size}|{st.st_mtime_ns}"

    def get(self, key: str | None) -> dict | None:
        return self.items.get(key) if self.path and key else None

    def put(self, key: str | None, item: dict) -> None:
        if self.path and key:
            self.items[key] = item

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"code": self.version, "items": self.items}), encoding="utf-8")


def _measure(adapter: Adapter, run: Run) -> Measured:
    try:
        values = adapter.measure(run)
        return Measured(run, {k: float(v) for k, v in values.items()} if values is not None else None)
    except Exception as e:  # one odd file shouldn't stop the whole backtest - it's reported (and counted) instead
        where = traceback.extract_tb(e.__traceback__)[-1]
        return Measured(run, None, error=f"{type(e).__name__}: {e} ({Path(where.filename).name}:{where.lineno})")


def applies(adapter: Adapter, run: Run) -> bool:
    """Does this adapter measure this run? An adapter that brings its own made-up runs measures
    those (and no one else's), and its runs are measured by no one else."""
    if run.source not in adapter.sources:
        return False
    if run.only_for:
        return run.only_for == adapter.name
    return not (adapter.synthetic and run.source == "synthetic")


def run_adapters(adapters: list[Adapter], runs: list[Run], cache: Cache | None = None,
                 progress: Callable[[str], None] | None = None) -> list[SourceResult]:
    """Measure every run with every adapter that applies, then summarise per adapter and source."""
    cache = cache or Cache(None)
    measured: dict[tuple[str, str], list[Measured]] = {}
    for i, run in enumerate(runs):
        todo = [a for a in adapters if applies(a, run)]
        if progress and run.path is not None:
            progress(f"[{i + 1}/{len(runs)}] {run.id}")
        for adapter in todo:
            key = Cache.key(adapter, run)
            hit = cache.get(key)
            if hit is not None:
                run.car, run.track = run.car or hit["car"], run.track or hit["track"]
                m = Measured(run, hit["values"], hit["error"])
            else:
                if not run.car and run.load is not None:  # e.g. a file not in a <driver>/<car>/<track> folder
                    try:
                        s = run.session()
                        run.car, run.track = s.car, s.track
                    except Exception:  # unreadable: measure() will say so
                        pass
                m = _measure(adapter, run)
                cache.put(key, {"values": m.values, "error": m.error, "car": run.car, "track": run.track})
            measured.setdefault((adapter.name, run.source), []).append(m)
        run.forget()
    cache.save()

    results = []
    for adapter in adapters:
        for source in SOURCES:
            ms = measured.get((adapter.name, source))
            if not ms:
                continue
            summary = adapter.summarise(ms)
            summary["runs_estimated_pct"] = 100 * sum(m.values is not None for m in ms) / len(ms)
            results.append(SourceResult(adapter, source, ms, summary))
    return results


# --- The gate -----------------------------------------------------------------------


def _number(x) -> float | None:
    return None if x is None or not math.isfinite(float(x)) else float(x)


RUNS_ESTIMATED = Check("runs_estimated_pct", "higher", tolerance=0.05)  # every adapter: fewer runs estimated = worse (0.05 for the baseline's rounding)


def gate(results: list[SourceResult], baseline: dict) -> bool:
    """Fill in each result's verdicts. True if everything passed."""
    for r in results:
        base = baseline.get(r.adapter.name, {}).get(r.source)
        if base and base.get("corpus") != fingerprint([m.run for m in r.measured]):
            r.notes.append(f"These runs aren't the ones the baseline was made on ({len(r.measured)} runs now, "
                           f"{base.get('runs')} then), so small differences are expected.")
        gated = {c.metric for c in r.adapter.checks} | {RUNS_ESTIMATED.metric}
        for check in [*r.adapter.checks, RUNS_ESTIMATED]:
            value = _number(r.summary.get(check.metric))
            was = _number(base["metrics"].get(check.metric)) if base else None
            if value is None and was is None:
                continue  # doesn't apply to these runs (e.g. needs a known answer)
            if value is None:
                status, why = "FAIL", "couldn't be worked out this time"
            elif check.limit is not None and check.worse(value, check.limit) > 0:
                status, why = "FAIL", f"past the hard limit {check.limit:g}"
            elif was is None:
                status, why = "new", "no baseline yet"
            elif check.worse(value, was) > check.tolerance:
                status, why = "FAIL", f"worse than the baseline by more than {check.tolerance:g}"
            elif check.worse(value, was) < -check.tolerance:
                status, why = "better", "better than the baseline: consider --update-baseline"
            else:
                status, why = "pass", f"within {check.tolerance:g} of the baseline"
            r.verdicts.append(Verdict(check.metric, value, was, status, why))
        for metric, value in r.summary.items():  # the rest are shown in the report but not gated
            if metric not in gated:
                r.verdicts.append(Verdict(metric, _number(value), None, "not gated"))
    return not any(r.failed for r in results)


# --- The baseline file --------------------------------------------------------------


def load_baseline(path: Path | None) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}


def _round(x: float) -> float | None:
    return None if x is None or not math.isfinite(x) else float(f"{x:.4g}")


def updated_baseline(baseline: dict, results: list[SourceResult]) -> dict:
    """The baseline with these results' numbers in place of the old ones (others are kept)."""
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in baseline.items()}
    out.setdefault("_about", "Accepted backtest numbers. Made by `fs-telemetry validate --update-baseline`; "
                             "a run worse than these by more than each check's tolerance fails.")
    for r in results:
        out.setdefault(r.adapter.name, {})[r.source] = {
            "runs": len(r.measured), "corpus": fingerprint([m.run for m in r.measured]),
            "metrics": {k: _round(v) for k, v in sorted(r.summary.items())},
        }
    return out


def save_baseline(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
