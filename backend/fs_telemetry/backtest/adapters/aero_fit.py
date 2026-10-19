"""Backtest adapter for the aero fit (aero.py): how well does it hold up?

AC doesn't log downforce, so there's no direct truth on real runs. What can be checked:
  * hold-out: fit on the odd laps only, then predict the tyre loads on the even laps.
    If the fit has really found the car's weight and ClA (rather than chasing noise), the
    loads it predicts on laps it never saw should match what AC logged.
  * speed bands: the hold-out error split by how fast the car was going. A fit that
    mis-models downforce shows up as a bias that grows with speed.
  * halves: the ClA from odd laps vs from even laps should be the same.
  * slow half vs fast half: ClA and balance fitted on the slower and the faster half of
    the straights. They should agree if ClA is constant; they don't quite (see the report).
  * short stretches: refit on 45-second windows. This is what the error bars (+/- ClA,
    +/- balance in aero.py) are checked against, and where `trustworthy` should flip.
  * kerbs: how much running the fit throws away for heavy vertical G, vs the fit quality.
  * drag: AC also logs each tyre's longitudinal force. The tyres push the car with (sum of their forces),
    the car accelerates by what it logs, and the difference is drag, which grows with speed squared:
    CdA = drag / (1/2 x air density x speed squared). It has nothing to do with the tyre-load fit, so it
    is a cross-check on the aero numbers: repeatable, and downforce-to-drag (ClA/CdA) plausible for the car.
    It also shows how far the aero balance could be off if the drag acts above/below the centre of gravity.
  * stability: runs of the same car and setup should give the same ClA and aero balance.
  * the .ldx lists the car's weight and static front weight: the fit finds both on its own.
  * synthetic runs: the right ClA and balance are known, and "more front wing" /
    "less rear wing" copies check the estimate moves the right way by about the right amount.

Checking ClA against the car's own aero files (data/aero.ini) needs the car's data folder. None of
the three cars (MFT02, MFT02 hybrid, supercars Mustang) is installed on the PC this was built on - AC's
cache holds only meshes and shadows for them - so there is no reference, and the truth checks on real
runs stay empty. When a car's unpacked data folder is in FS Telemetry/cars, comparing with it is what to add here.
"""

from __future__ import annotations

import numpy as np

from ... import aero
from .. import metrics as m
from ..corpus import Run
from ..harness import Adapter, Check, Measured, column, perturbation_pairs

# Speed bands for the hold-out, as a share of the run's top straight-line speed so cars can be compared
BANDS = ((0.0, 0.5, "slow"), (0.5, 0.8, "mid"), (0.8, 1.01, "fast"))
WINDOW_S = 45  # length of the short stretches the error bars are checked on
WINDOW_STARTS = (0.1, 0.45, 0.8)  # where in the run they start (share of the run's length)


def _within(part: aero.AeroCalibration, full: aero.AeroCalibration) -> tuple[float, float, float, float]:
    """A partial fit vs the whole-run fit: (ClA error %, balance error pp, ClA within its own +/-, balance within its +/-)."""
    cla = 100 * abs(part.coefficient_m2 - full.coefficient_m2) / full.coefficient_m2
    bal = abs(part.balance_front_pct - full.balance_front_pct)
    return cla, bal, float(cla <= part.cla_error_pct), float(bal <= part.balance_error_pp)


def drag_area(s, x: aero._Signals, cal: aero.AeroCalibration) -> float | None:
    """CdA (m2) from the tyres' longitudinal forces: drag = (sum of tyre forces) - (mass x acceleration).
    Fitted against 1/2 rho v^2 with a constant for rolling drag. None if the tyre forces aren't logged."""
    names = [f"Tire Long Fx {c}" for c in aero.CORNERS]
    if not all(n in s.channels for n in names):
        return None
    push = sum(np.interp(x.t, s.channels[n].time, s.channels[n].data) for n in names)
    drag = push - cal.mass_kg * aero.G * x.ax
    use = aero.straight_running(s, x) & (x.v > aero.MIN_AERO_SPEED)
    if use.sum() < 300:
        return None
    q = 0.5 * x.rho * x.v**2
    (cda, _), _, _ = aero._robust_fit(np.c_[q[use], np.ones(use.sum())], drag[use])
    return float(cda) if cda > 0 else None


def measure(run: Run) -> dict[str, float] | None:
    s = run.session()
    x = aero._signals(s)
    full = aero.calibrate(s, x) if x else None
    if full is None:
        return None  # no tyre loads (iRacing) or too little straight running
    cal, _ = full
    odd, even = m.lap_halves(s, x.t)
    on_odd, on_even = aero.calibrate(s, x, only=odd), aero.calibrate(s, x, only=even)
    out = {"cla_m2": cal.coefficient_m2, "balance_front_pct": cal.balance_front_pct, "mass_kg": cal.mass_kg,
           "fit_error_n": cal.fit_error_n, "trustworthy": float(cal.trustworthy),
           "stated_cla_error_pct": cal.cla_error_pct, "stated_balance_error_pp": cal.balance_error_pp,
           "fit_error_pct_of_weight": 100 * cal.fit_error_n / cal.weight_n}

    straight = aero.straight_running(s, x)
    kph = x.v * 3.6
    moving = x.v > 3
    out["kerb_excluded_pct"] = 100 * float(np.mean(np.abs(x.az[moving]) >= 0.3)) if moving.any() else float("nan")

    cda = drag_area(s, x, cal)
    if cda:
        out["cda_m2"] = cda
        out["cla_over_cda"] = cal.coefficient_m2 / cda
        try:  # balance shift if the drag acts 10 cm above the centre of gravity (the fit assumes it acts AT it)
            out["balance_shift_pp_per_10cm_drag_height"] = 100 * cda * 0.1 / (float(s.details["Vehicle Wheelbase"]) * cal.coefficient_m2)
        except (KeyError, ValueError):
            pass

    # Slow half vs fast half of the straights (split at the median speed)
    median_kph = float(np.median(kph[straight]))
    slow, fast = aero.calibrate(s, x, only=kph < median_kph), aero.calibrate(s, x, only=kph >= median_kph)
    if slow and fast:
        out["fast_minus_slow_cla_pct"] = 100 * (fast[0].coefficient_m2 - slow[0].coefficient_m2) / cal.coefficient_m2
        out["fast_minus_slow_balance_pp"] = fast[0].balance_front_pct - slow[0].balance_front_pct

    # Short stretches: how far off, whether the stated +/- covered it, and whether it was flagged as doubtful
    windows = []
    for start in WINDOW_STARTS:
        t0 = start * s.duration
        part = aero.calibrate(s, x, only=(x.t >= t0) & (x.t < t0 + WINDOW_S))
        windows.append(part[0] if part else None)
    made = [w for w in windows if w]
    out["window_fits_pct"] = 100 * len(made) / len(windows)
    if made:
        errors = np.array([_within(w, cal) for w in made])
        flagged = np.array([w.trustworthy for w in made])
        out["window_cla_error_pct"] = errors[:, 0].mean()
        out["window_balance_error_pp"] = errors[:, 1].mean()
        out["window_cla_covered"] = errors[:, 2].mean()
        out["window_balance_covered"] = errors[:, 3].mean()
        out["window_trusted_share"] = flagged.mean()
        if flagged.any():
            out["window_cla_error_pct_trusted"] = errors[flagged, 0].mean()
        if (~flagged).any():
            out["window_cla_error_pct_doubtful"] = errors[~flagged, 0].mean()

    if on_odd and on_even:
        held, k = on_odd
        check = straight & even
        total, front = aero.model_loads(held, k, x)
        pct = 100 / cal.weight_n  # loads as a % of the car's weight, so cars can be compared
        out |= {
            "holdout_total_rmse_pct": m.rmse(total[check], x.total[check]) * pct,
            "holdout_total_bias_pct": m.bias(total[check], x.total[check]) * pct,
            "holdout_total_r2": m.r2(total[check], x.total[check]),
            "holdout_front_rmse_pct": m.rmse(front[check], x.front[check]) * pct,
            "halves_cla_diff_pct": 100 * abs(held.coefficient_m2 - on_even[0].coefficient_m2) / cal.coefficient_m2,
            "halves_balance_diff_pp": abs(held.balance_front_pct - on_even[0].balance_front_pct),
        }
        # the same hold-out split by speed
        top = float(np.max(x.v[check])) if check.any() else 0.0
        for lo, hi, name in BANDS:
            band = check & (x.v >= lo * top) & (x.v < hi * top)
            if band.sum() >= 30:
                out[f"holdout_{name}_total_bias_pct"] = m.bias(total[band], x.total[band]) * pct
                out[f"holdout_{name}_front_bias_pct"] = m.bias(front[band], x.front[band]) * pct
        # each half checked against its own +/-
        covered = [_within(h, cal) for h in (on_odd[0], on_even[0])]
        out["half_cla_covered"] = float(np.mean([c[2] for c in covered]))
        out["half_balance_covered"] = float(np.mean([c[3] for c in covered]))

    # Truth: synthetic runs know everything; real AC runs know the weight from the .ldx
    truth = dict(run.truth)
    try:
        truth.setdefault("mass_kg", float(s.details["Vehicle Weight"]))
        truth.setdefault("static_front_pct", 100 * float(s.details["Vehicle COG"]))
    except (KeyError, ValueError):
        pass
    if "mass_kg" in truth:
        out["mass_error_pct"] = 100 * (cal.mass_kg - truth["mass_kg"]) / truth["mass_kg"]
    if "static_front_pct" in truth:
        out["static_front_error_pp"] = cal.static_front_pct - truth["static_front_pct"]
    if "cla_m2" in truth:
        out["true_cla_m2"] = truth["cla_m2"]
        out["cla_error_pct"] = 100 * (cal.coefficient_m2 - truth["cla_m2"]) / truth["cla_m2"]
    if "balance_front_pct" in truth:
        out["true_balance_front_pct"] = truth["balance_front_pct"]
        out["balance_error_pp"] = cal.balance_front_pct - truth["balance_front_pct"]
    return out


def _finite(ms, key) -> np.ndarray:
    v = column(ms, key)
    return v[np.isfinite(v)]


def _median(ms, key):
    v = _finite(ms, key)
    return float(np.median(v)) if len(v) else float("nan")


def _mean_pct(ms, *keys) -> float:
    """Average of 0/1 columns, as a %, over every run and key (NaN if none exist)."""
    v = np.concatenate([_finite(ms, k) for k in keys])
    return 100 * float(v.mean()) if len(v) else float("nan")


def _worst_abs(ms, key):
    v = np.abs(column(ms, key))
    return float(np.nanmax(v)) if np.isfinite(v).any() else float("nan")


def _per_car(ms, key) -> dict[str, float]:
    """The median of one measured number for each car; cars with none are left out."""
    out = {}
    for car in sorted({x.run.car for x in ms if x.values}):
        v = _finite([x for x in ms if x.run.car == car], key)
        if len(v):
            out[car] = float(np.median(v))
    return out


def _spreads(ms, key, stat) -> dict[str, float]:
    """Run-to-run spread of one number, for each group of runs with the same car and setup."""
    ok = [x for x in ms if x.values]
    return {g: v[stat] for g, v in m.spread_by_group([x.run.group for x in ok], column(ok, key)).items()}


def summarise(ms: list[Measured]) -> dict[str, float]:
    out = {
        "holdout_total_rmse_pct_median": _median(ms, "holdout_total_rmse_pct"),
        "holdout_total_r2_median": _median(ms, "holdout_total_r2"),
        "holdout_front_rmse_pct_median": _median(ms, "holdout_front_rmse_pct"),
        "halves_cla_diff_pct_median": _median(ms, "halves_cla_diff_pct"),
        "halves_balance_diff_pp_median": _median(ms, "halves_balance_diff_pp"),
        "mass_error_pct_worst": _worst_abs(ms, "mass_error_pct"),
        "static_front_error_pp_worst": _worst_abs(ms, "static_front_error_pp"),
        "trustworthy_pct": 100 * float(np.nanmean(column(ms, "trustworthy"))) if any(x.values for x in ms) else 0.0,
    }
    # stability: gated on the worst group, every group shown
    for key, stat, name in (("cla_m2", "cv_pct", "cla_cv_pct"), ("balance_front_pct", "std", "balance_std_pp")):
        spreads = _spreads(ms, key, stat)
        out[f"{name}_worst_group"] = max(spreads.values(), default=float("nan"))
        out |= {f"{name} [{group}]": value for group, value in spreads.items()}

    # hold-out bias by speed, per car (median over runs): does the error grow with speed?
    biases = []
    for _, _, name in BANDS:
        for part in ("total", "front"):
            per_car = _per_car(ms, f"holdout_{name}_{part}_bias_pct")
            out |= {f"holdout_{name}_{part}_bias_pct [{car}]": v for car, v in per_car.items()}
            biases += [abs(v) for v in per_car.values()]
    out["holdout_band_bias_pct_worst"] = max(biases, default=float("nan"))

    # constant-ClA check: fast half vs slow half (+ = reads higher when fast)
    for key in ("fast_minus_slow_cla_pct", "fast_minus_slow_balance_pp"):
        per_car = _per_car(ms, key)
        out |= {f"{key} [{car}]": v for car, v in per_car.items()}
        out[f"{key}_worst_car"] = max((abs(v) for v in per_car.values()), default=float("nan"))

    # drag cross-check: repeatable between runs of the same car? what downforce-to-drag does it imply?
    cda_spread = _spreads(ms, "cda_m2", "cv_pct")
    out["cda_cv_pct_worst_group"] = max(cda_spread.values(), default=float("nan"))
    out |= {f"cda_cv_pct [{g}]": v for g, v in cda_spread.items()}
    for key in ("cda_m2", "cla_over_cda", "balance_shift_pp_per_10cm_drag_height"):
        out |= {f"{key} [{car}]": v for car, v in _per_car(ms, key).items()}

    # the error bars: what they say, how far off the 45 s windows really were, and how often the bar covered it
    for key in ("stated_cla_error_pct", "stated_balance_error_pp", "window_cla_error_pct", "window_balance_error_pp",
                "kerb_excluded_pct", "fit_error_pct_of_weight"):
        out |= {f"{key} [{car}]": v for car, v in _per_car(ms, key).items()}
    out["error_bar_cla_covered_pct"] = _mean_pct(ms, "half_cla_covered", "window_cla_covered")
    out["error_bar_balance_covered_pct"] = _mean_pct(ms, "half_balance_covered", "window_balance_covered")
    out["window_trusted_share_pct"] = _mean_pct(ms, "window_trusted_share")
    trusted = _finite(ms, "window_cla_error_pct_trusted")
    out["window_cla_error_pct_trusted_p90"] = float(np.percentile(trusted, 90)) if len(trusted) else float("nan")
    doubtful = _finite(ms, "window_cla_error_pct_doubtful")
    out["window_cla_error_pct_doubtful_mean"] = float(doubtful.mean()) if len(doubtful) else float("nan")

    if np.isfinite(column(ms, "true_cla_m2")).any():  # runs with a known answer
        out |= {
            "cla_error_pct_worst": _worst_abs(ms, "cla_error_pct"),
            "balance_error_pp_worst": _worst_abs(ms, "balance_error_pp"),
            # of the runs whose true answer differs, the share of pairs put in the right order
            "balance_pairs_right_order_pct": 100 * m.pair_agreement(column(ms, "balance_front_pct"),
                                                                    column(ms, "true_balance_front_pct")),
            "cla_pairs_right_order_pct": 100 * m.pair_agreement(column(ms, "cla_m2"), column(ms, "true_cla_m2")),
        }
    pairs = perturbation_pairs(ms)
    if pairs:
        moves = [m.moved_as_expected(a.values[key], b.values[key], b.values[f"true_{key}"] - a.values[f"true_{key}"])
                 for a, b in pairs for key in ("cla_m2", "balance_front_pct")]
        out["perturbation_right_way_pct"] = 100 * float(np.mean([mv["right_way"] for mv in moves]))
        out["perturbation_size_error_pct_worst"] = max(100 * abs(mv["size_ratio"] - 1) for mv in moves)
    return out


ADAPTER = Adapter(
    name="aero_fit",
    title="Aero fit (ClA, aero balance, weight) - aero.py",
    sources=("synthetic", "ac"),  # iRacing doesn't log tyre loads, so there's no aero estimate to check
    measure=measure,
    summarise=summarise,
    notes=("Fit on odd laps, predict tyre loads on even laps (as % of the car's weight), also split by speed; "
           "slow vs fast half of the straights; refits on 45 s stretches to check the +/- error bars and when "
           "'trustworthy' flips; ClA and balance spread between runs of the same car and setup; weight vs the .ldx. "
           "AC files don't record the setup, so unless runs are labelled in corpus.json the spread also includes "
           "any setup changes. No reference ClA/balance: the cars' aero.ini files aren't on this PC."),
    checks=[
        Check("holdout_total_rmse_pct_median", "lower", tolerance=0.3),
        Check("holdout_total_r2_median", "higher", tolerance=0.02),
        Check("holdout_front_rmse_pct_median", "lower", tolerance=0.3),
        Check("halves_cla_diff_pct_median", "lower", tolerance=0.5),
        Check("cla_cv_pct_worst_group", "lower", tolerance=1.0),
        Check("balance_std_pp_worst_group", "lower", tolerance=1.0),
        Check("mass_error_pct_worst", "lower", tolerance=0.5, limit=5.0),
        Check("trustworthy_pct", "higher", tolerance=0.0),
        Check("holdout_band_bias_pct_worst", "lower", tolerance=0.3, limit=3.0),
        Check("cda_cv_pct_worst_group", "lower", tolerance=3.0),
        Check("fast_minus_slow_cla_pct_worst_car", "lower", tolerance=3.0),
        Check("fast_minus_slow_balance_pp_worst_car", "lower", tolerance=3.0),
        Check("error_bar_cla_covered_pct", "higher", tolerance=5.0, limit=70.0),
        Check("error_bar_balance_covered_pct", "higher", tolerance=5.0, limit=70.0),
        Check("window_cla_error_pct_trusted_p90", "lower", tolerance=1.0, limit=12.0),
        Check("cla_error_pct_worst", "lower", tolerance=0.5, limit=3.0),
        Check("balance_error_pp_worst", "lower", tolerance=0.5, limit=2.0),
        Check("balance_pairs_right_order_pct", "higher", tolerance=0.0, limit=100.0),
        Check("cla_pairs_right_order_pct", "higher", tolerance=0.0, limit=100.0),
        Check("perturbation_right_way_pct", "higher", tolerance=0.0, limit=100.0),
        Check("perturbation_size_error_pct_worst", "lower", tolerance=5.0, limit=25.0),
    ],
)
