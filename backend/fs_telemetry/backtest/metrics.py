"""The numbers a backtest reports: how far an estimate is from the truth, and
helpers for the usual ways of checking an estimate when there is no truth.

Error between an estimate and the truth (or a prediction and what happened):
    bias   average error (+ = estimate too high). A steady offset.
    MAE    average size of the error, ignoring its sign
    RMSE   like MAE but big misses count more
    R²     share of the ups and downs the estimate explains (1 = all, 0 = no better than the average)

Ranking - often what engineers really use an estimate for ("is setup B more
front-limited than A?"), and an estimate can rank well even with a steady bias:
    Spearman   correlation of the ranks (1 = same order, -1 = reversed)
    Kendall    from counting pairs put in the same / the opposite order
    pair_agreement   share of pairs put in the right order: "picks the right one of two X% of the time"

Checks without a truth:
    hold-out      fit on some laps (odd), predict the others (even)            -> lap_halves
    stability     the same car and setup should give the same answer every run -> spread_by_group
    perturbation  change one known thing: the estimate should move that way    -> moved_as_expected

Everything ignores samples where either side is missing (NaN), and returns NaN
when there's too little left to say anything.
"""

from __future__ import annotations

import numpy as np


def _pair(estimate, truth) -> tuple[np.ndarray, np.ndarray]:
    e, t = np.asarray(estimate, float).ravel(), np.asarray(truth, float).ravel()
    ok = np.isfinite(e) & np.isfinite(t)
    return e[ok], t[ok]


def bias(estimate, truth) -> float:
    e, t = _pair(estimate, truth)
    return float(np.mean(e - t)) if len(e) else float("nan")


def mae(estimate, truth) -> float:
    e, t = _pair(estimate, truth)
    return float(np.mean(np.abs(e - t))) if len(e) else float("nan")


def rmse(estimate, truth) -> float:
    e, t = _pair(estimate, truth)
    return float(np.sqrt(np.mean((e - t) ** 2))) if len(e) else float("nan")


def r2(estimate, truth) -> float:
    e, t = _pair(estimate, truth)
    total = np.sum((t - t.mean()) ** 2) if len(t) else 0.0
    return float(1 - np.sum((t - e) ** 2) / total) if total > 0 else float("nan")


def ranks(a) -> np.ndarray:
    """1 = smallest. Equal values share the average of their places (so 5, 7, 7 -> 1, 2.5, 2.5)."""
    a = np.asarray(a, float)
    order = np.argsort(a, kind="stable")
    place = np.empty(len(a))
    place[order] = np.arange(1, len(a) + 1)
    _, group, count = np.unique(a, return_inverse=True, return_counts=True)
    return (np.bincount(group, weights=place) / count)[group]


def spearman(estimate, truth) -> float:
    e, t = _pair(estimate, truth)
    if len(e) < 3:
        return float("nan")
    re, rt = ranks(e), ranks(t)
    if re.std() == 0 or rt.std() == 0:
        return float("nan")
    return float(np.corrcoef(re, rt)[0, 1])


def _pair_signs(estimate, truth) -> tuple[np.ndarray, np.ndarray]:
    """For every pair of items (i < j): which way the estimate and the truth say they're ordered."""
    e, t = _pair(estimate, truth)
    i, j = np.triu_indices(len(e), k=1)
    return np.sign(e[j] - e[i]), np.sign(t[j] - t[i])


def kendall(estimate, truth) -> float:
    """Kendall's tau-b (the version that allows for ties)."""
    se, st = _pair_signs(estimate, truth)
    scale = np.sqrt(np.count_nonzero(se) * np.count_nonzero(st))
    return float(np.sum(se * st) / scale) if scale > 0 else float("nan")


def pair_agreement(estimate, truth) -> float:
    """Of the pairs the truth tells apart, the share the estimate puts in the same order (a tie counts as wrong)."""
    se, st = _pair_signs(estimate, truth)
    judged = st != 0
    return float(np.mean(se[judged] == st[judged])) if judged.any() else float("nan")


# --- Checks without a truth ---------------------------------------------------------


def lap_halves(session, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(odd laps, even laps) as True/False per sample at times t. Fit on one, check on the other:
    alternate laps see the same corners and speeds, so a good fit should predict the other half."""
    lap_no = np.zeros(len(t), int)
    for lap in session.laps:
        lap_no[(t >= lap.start) & (t < lap.end)] = lap.number
    return (lap_no % 2 == 1), (lap_no > 0) & (lap_no % 2 == 0)


def spread_by_group(groups: list[str], values) -> dict[str, dict[str, float]]:
    """How much one number varies between runs of the same car and setup.
    Only groups with at least two runs. std is in the number's units; cv_pct = std as a % of the mean."""
    values = np.asarray(values, float)
    out = {}
    for g in sorted(set(groups)):
        v = values[[x == g for x in groups]]
        v = v[np.isfinite(v)]
        if len(v) < 2:
            continue
        mean, std = float(v.mean()), float(v.std(ddof=1))
        out[g] = {"runs": len(v), "mean": mean, "std": std, "range": float(v.max() - v.min()),
                  "cv_pct": 100 * std / abs(mean) if mean else float("nan")}
    return out


def moved_as_expected(before: float, after: float, expected_change: float) -> dict[str, float | bool]:
    """After changing one known thing (e.g. more front wing): did the estimate move the expected way,
    and by how much compared with what was expected (size_ratio 1 = exactly right)?"""
    change = after - before
    return {"change": change, "right_way": bool(np.sign(change) == np.sign(expected_change) != 0),
            "size_ratio": change / expected_change if expected_change else float("nan")}
