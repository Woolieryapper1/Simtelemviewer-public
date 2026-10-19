"""Backtests: how good are the app's ESTIMATES (aero, balance, ...)?

Several channels are inferred rather than measured. This package runs each
estimator over a corpus of runs - your real telemetry plus made-up runs with a
known answer - and reports how far off it is, so a change to an estimator can be
judged by numbers. Run it with `fs-telemetry validate` (or `pytest -m backtest`).

    metrics.py    bias, RMSE, R², rank agreement; hold-out / stability / perturbation helpers
    corpus.py     which runs: your telemetry folders (never copied) and the synthetic runs
    synthetic.py  the made-up runs with known answers
    harness.py    the Adapter interface, the runner, the baseline and the gate
    report.py     the markdown / HTML report
    adapters/     one small module per estimator
"""
