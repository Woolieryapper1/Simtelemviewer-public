"""Backtest harness tests: the metrics, the gate (a broken estimator must fail it), the
report and the command. Synthetic runs only, so they're quick - except the last test,
which runs over your real telemetry (pytest -m backtest)."""

import json

import numpy as np
import pytest

from fs_telemetry import aero, cli
from fs_telemetry.backtest import metrics as m
from fs_telemetry.backtest.adapters import ADAPTERS
from fs_telemetry.backtest.corpus import Run, load_manifest, real_runs
from fs_telemetry.backtest.harness import Adapter, Check, gate, load_baseline, run_adapters, updated_baseline
from fs_telemetry.backtest.synthetic import synthetic_runs
from fs_telemetry.backtest.validate import BASELINE, CORPUS, validate

# --- Metrics -------------------------------------------------------------------------


def test_error_metrics():
    est, truth = [1.0, 2.0, 3.0, 6.0], [1.0, 2.0, 3.0, 4.0]
    assert m.bias(est, truth) == pytest.approx(0.5)
    assert m.mae(est, truth) == pytest.approx(0.5)
    assert m.rmse(est, truth) == pytest.approx(1.0)
    assert m.r2(truth, truth) == 1.0
    assert m.r2([2.5] * 4, truth) == pytest.approx(0.0)  # always guessing the average explains nothing
    assert m.rmse([1, np.nan], [1, 5]) == 0.0  # missing samples are ignored
    assert np.isnan(m.rmse([], []))


def test_rank_metrics():
    truth = [1, 2, 3, 4, 5]
    assert m.spearman([10, 20, 30, 40, 50], truth) == pytest.approx(1)
    assert m.spearman([5, 4, 3, 2, 1], truth) == pytest.approx(-1)
    assert m.kendall([1, 2, 3, 5, 4], truth) == pytest.approx(0.8)  # 9 of 10 pairs right: (9 - 1) / 10
    assert m.pair_agreement([1, 2, 3, 5, 4], truth) == pytest.approx(0.9)
    assert list(m.ranks([5, 7, 7, 1])) == [2, 3.5, 3.5, 1]
    # pairs the truth can't tell apart don't count for or against
    assert m.pair_agreement([1, 2, 3], [0, 0, 1]) == 1.0


def test_check_helpers():
    groups = ["a", "a", "a", "b", "c", "c"]
    spread = m.spread_by_group(groups, [1.0, 2.0, 3.0, 9.0, 4.0, 4.0])
    assert set(spread) == {"a", "c"}  # one run on its own has no spread
    assert spread["a"]["std"] == pytest.approx(1.0) and spread["a"]["cv_pct"] == pytest.approx(50)
    assert m.moved_as_expected(10, 12, 2)["right_way"] and m.moved_as_expected(10, 12, 2)["size_ratio"] == 1
    assert not m.moved_as_expected(10, 9, 2)["right_way"]
    s = synthetic_runs()[0].session()
    t = np.arange(0, 300, 0.5)
    odd, even = m.lap_halves(s, t)
    assert odd[0] and not even[0] and even[t == 90][0] and not (odd & even).any()  # laps are 60 s


# --- Gate ----------------------------------------------------------------------------


def synthetic_results(adapters=None):
    return run_adapters(adapters or ADAPTERS, synthetic_runs())


def test_synthetic_runs_pass_the_committed_baseline():
    results = synthetic_results()
    assert gate(results, load_baseline(BASELINE)), [(v.metric, v.value, v.why) for r in results
                                                     for v in r.verdicts if v.status == "FAIL"]
    aero_numbers = next(r for r in results if r.adapter.name == "aero_fit").summary
    assert aero_numbers["cla_error_pct_worst"] < 1 and aero_numbers["balance_error_pp_worst"] < 0.5
    assert aero_numbers["perturbation_right_way_pct"] == 100


def test_a_broken_estimator_fails_the_gate(monkeypatch):
    """Make the aero fit 10% optimistic on ClA: the gate must catch it."""
    real = aero.calibrate

    def broken(*args, **kwargs):
        result = real(*args, **kwargs)
        if result is None:
            return None
        cal, k = result
        return cal.model_copy(update={"coefficient_m2": cal.coefficient_m2 * 1.1}), k

    monkeypatch.setattr(aero, "calibrate", broken)
    results = synthetic_results()
    assert not gate(results, load_baseline(BASELINE))
    failed = {v.metric for r in results for v in r.verdicts if v.status == "FAIL"}
    assert {"cla_error_pct_worst", "holdout_total_rmse_pct_median"} <= failed


def test_a_crashing_or_silent_estimator_fails_the_gate():
    base = next(a for a in ADAPTERS if a.name == "aero_fit")

    def crash(run):
        raise ValueError("boom")

    for measure in (crash, lambda run: None):
        adapter = Adapter(base.name, base.title, base.sources, measure, base.summarise, base.checks)
        results = synthetic_results([adapter])
        assert not gate(results, load_baseline(BASELINE))
        assert results[0].summary["runs_estimated_pct"] == 0
        if measure is crash:  # a crash is reported, not raised
            assert results[0].measured[0].error.startswith("ValueError: boom")


def test_gate_rules():
    adapter = Adapter("demo", "Demo", ("synthetic",), lambda run: {"err": run.truth["err"]},
                      lambda ms: {"err": max(x.values["err"] for x in ms)},
                      [Check("err", "lower", tolerance=0.5, limit=10)])

    def results(err):
        return run_adapters([adapter], [Run(id="r", source="synthetic", car="c", truth={"err": err})])

    baseline = updated_baseline({}, results(2.0))

    def status(err):
        r = results(err)
        gate(r, baseline)
        return next(v.status for v in r[0].verdicts if v.metric == "err")

    assert status(2.4) == "pass"
    assert status(2.6) == "FAIL"  # worse than the baseline by more than the tolerance
    assert status(1.0) == "better"
    assert status(11) == "FAIL"  # past the hard limit
    assert not gate(results(11), {})  # the hard limit applies even without a baseline


# --- Corpus, report and command ---------------------------------------------------


def test_corpus_skips_missing_folders_and_reads_the_manifest(tmp_path):
    (tmp_path / "Me" / "some_car" / "some_track").mkdir(parents=True)
    (tmp_path / "Me" / "some_car" / "some_track" / "run-1.ld").write_bytes(b"")
    (tmp_path / "Me" / "some_car" / "some_track" / "run-old.ld").write_bytes(b"")
    manifest_file = tmp_path / "corpus.json"
    manifest_file.write_text(json.dumps({"folders": {"ac": str(tmp_path), "iracing": str(tmp_path / "nowhere")},
                                         "exclude": ["*-old"], "setups": {"run-*": "wing 3"}}))
    manifest = load_manifest(manifest_file)
    runs = real_runs(manifest, {"ac", "iracing"})
    assert [(r.id, r.car, r.track, r.group) for r in runs] == [("run-1", "some_car", "some_track", "some_car / wing 3")]
    assert load_manifest(CORPUS).folders["ac"] is not None  # the committed one uses the app's folders


def test_validate_writes_a_report_and_a_baseline(tmp_path):
    out = validate(synthetic_only=True, out=tmp_path / "report", baseline=tmp_path / "baseline.json",
                   update_baseline=True, write_html=True)
    assert out.passed  # no baseline yet: only the hard limits apply
    report = (tmp_path / "report" / "report.md").read_text(encoding="utf-8")
    assert "gate: **PASS**" in report and "synthetic-baseline-with-off" in report and "| new |" in report
    assert "<table>" in (tmp_path / "report" / "report.html").read_text(encoding="utf-8")
    saved = json.loads((tmp_path / "baseline.json").read_text())
    assert saved["aero_fit"]["synthetic"]["runs"] == 5
    again = validate(synthetic_only=True, out=tmp_path / "report", baseline=tmp_path / "baseline.json")
    assert again.passed and "| pass |" in again.report.read_text(encoding="utf-8")


def test_command_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(aero, "calibrate", lambda *a, **k: None)  # an estimator that never answers
    with pytest.raises(SystemExit) as stop:
        cli.main(["validate", "--synthetic-only", "--out", str(tmp_path), "--baseline", str(BASELINE)])
    assert stop.value.code == 1


# --- Real telemetry (pytest -m backtest) -----------------------------------------


@pytest.mark.backtest
def test_real_corpus_against_the_baseline(tmp_path):
    outcome = validate(out=tmp_path)
    real = [r for r in outcome.results if r.source != "synthetic"]
    if not real:
        pytest.skip("no telemetry files on this PC")
    print(outcome.report.read_text(encoding="utf-8"))
    assert outcome.passed, [(r.adapter.name, r.source, v.metric, v.value, v.baseline, v.why)
                            for r in outcome.results for v in r.verdicts if v.status == "FAIL"]


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_metrics_cannot_pass_a_known_gate(value):
    results = synthetic_results()
    results[0].summary["cla_error_pct_worst"] = value
    assert not gate(results, load_baseline(BASELINE))
