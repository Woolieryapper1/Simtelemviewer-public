"""`fs-telemetry validate`: run the backtest, write the report, apply the gate.

    fs-telemetry validate                    every estimator, synthetic + your real runs
    fs-telemetry validate --synthetic-only   just the made-up runs (quick, needs no files)
    fs-telemetry validate --only aero_fit    one estimator
    fs-telemetry validate --update-baseline  accept these numbers as the new baseline
    fs-telemetry validate --html             a web page of the report too

Exit code 0 = gate passed, 1 = something got worse than the baseline.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from .adapters import ADAPTERS
from .corpus import load_manifest, real_runs
from .harness import Adapter, Cache, SourceResult, gate, load_baseline, run_adapters, save_baseline, updated_baseline
from .report import html_page, markdown
from .synthetic import synthetic_runs

_BACKEND = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).with_name("baseline.json")  # committed: the accepted numbers
CORPUS = _BACKEND / "tests" / "backtest" / "corpus.json"  # committed: which folders, how runs are grouped
# Reports go in backtest-report/ in the project folder (git-ignored); if the app is
# installed without the project folder, in the app's data folder instead.
OUT = _BACKEND.parent / "backtest-report" if (_BACKEND.parent / "pyproject.toml").exists() \
    else config.DATA_DIR / "backtest-report"


@dataclass
class Outcome:
    passed: bool
    results: list[SourceResult]
    report: Path
    messages: list[str] = field(default_factory=list)


def validate(adapters: list[Adapter] | None = None, synthetic_only: bool = False, corpus: Path | None = CORPUS,
             baseline: Path = BASELINE, out: Path = OUT, update_baseline: bool = False, write_html: bool = False,
             use_cache: bool = True, progress=None) -> Outcome:
    adapters = adapters if adapters is not None else ADAPTERS
    messages = []
    runs = synthetic_runs() + [r for a in adapters if a.synthetic for r in a.synthetic()]
    if not synthetic_only:
        manifest = load_manifest(corpus)
        sims = {s for a in adapters for s in a.sources} - {"synthetic"}
        found = real_runs(manifest, sims)
        runs += found
        for sim in sorted(sims):
            folder = manifest.folders.get(sim)
            n = sum(r.source == sim for r in found)
            messages.append(f"{sim}: {n} runs from {folder}" if n else
                            f"{sim}: no runs ({folder or 'switched off'} {'is empty' if folder and folder.exists() else 'not found'}) - skipped")

    cache = Cache(out / ".cache.json" if use_cache else None)
    results = run_adapters(adapters, runs, cache, progress)
    known = load_baseline(baseline)
    passed = gate(results, known)
    if update_baseline:
        save_baseline(baseline, updated_baseline(known, results))
        messages.append(f"Baseline updated: {baseline}")

    out.mkdir(parents=True, exist_ok=True)
    report = out / "report.md"
    report.write_text(markdown(results, passed, messages), encoding="utf-8")
    if write_html:
        (out / "report.html").write_text(html_page(results, passed, messages), encoding="utf-8")
    return Outcome(passed, results, report, messages)


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--synthetic-only", action="store_true", help="only the made-up runs (no telemetry files needed)")
    p.add_argument("--only", action="append", metavar="NAME",
                   help=f"just this estimator (can repeat): {', '.join(a.name for a in ADAPTERS)}")
    p.add_argument("--corpus", type=Path, default=CORPUS, help="corpus file: which folders to use (default: %(default)s)")
    p.add_argument("--baseline", type=Path, default=BASELINE, help="baseline file (default: %(default)s)")
    p.add_argument("--out", type=Path, default=OUT, help="where the report goes (default: %(default)s)")
    p.add_argument("--update-baseline", action="store_true", help="accept this run's numbers as the new baseline")
    p.add_argument("--html", action="store_true", help="also write report.html")
    p.add_argument("--no-cache", action="store_true", help="re-run every file even if nothing changed")


def run_from_args(args: argparse.Namespace) -> int:
    adapters = ADAPTERS
    if args.only:
        unknown = set(args.only) - {a.name for a in ADAPTERS}
        if unknown:
            print(f"Unknown estimator(s): {', '.join(sorted(unknown))}")
            return 2
        adapters = [a for a in ADAPTERS if a.name in args.only]
    outcome = validate(adapters, args.synthetic_only, args.corpus, args.baseline, args.out, args.update_baseline,
                       args.html, not args.no_cache, progress=lambda line: print("  " + line, flush=True))
    print()
    for line in outcome.messages:
        print("  " + line)
    for r in outcome.results:
        bad = [v for v in r.verdicts if v.status == "FAIL"]
        print(f"  {r.adapter.name} on {r.source} ({len(r.measured)} runs): {'FAIL' if bad else 'pass'}")
        for v in bad:
            print(f"      {v.metric} = {v.value} (baseline {v.baseline}): {v.why}")
    print(f"\n  Gate: {'PASS' if outcome.passed else 'FAIL'}. Report: {outcome.report}\n")
    return 0 if outcome.passed or args.update_baseline else 1
