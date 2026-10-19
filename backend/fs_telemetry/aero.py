"""Aero loads, ESTIMATED from the tyre loads AC logs.

These are approximations: AC doesn't log aero loads, so they're inferred with a
fit (below). Every channel this module publishes is named "... (est.)" and
flagged approximate=True, and the aero map says so too.

AC doesn't log downforce, but it logs the vertical load on each tyre. Weight
transfer (braking, cornering) only MOVES load between tyres - the total stays
the car's weight - so whatever total load is left over is aero:

    total tyre load = weight x (1 + vertical G)  +  ClA x q
    front tyre load = front weight x (1 + vertical G)  +  k x longitudinal G  +  ClA_front x q

    q   = dynamic pressure = 1/2 x air density x speed²
    ClA = downforce coefficient (m²); downforce = ClA x q
    k   = how much front load moves with braking/acceleration (= -weight x CG height / wheelbase)
    vertical G covers bumps and kerbs pushing the car up and down

Calibration: the unknowns (weight, front weight, k, ClA, ClA_front) are found by
a least-squares fit over all the run's straight-line running - thousands of
samples - then any sample more than 3 standard deviations off is dropped and the
fit is redone (so an off or a spin doesn't skew it). With those, every sample
gets its own downforce, front/rear split and balance.

How far to trust it: every calibration carries error bars (cla_error_pct, balance_error_pp) from the
fit's standard error, scaled to match what the backtest saw refitting short stretches of real runs.
Whole real runs: stated ClA +/-1.4 to 2.1%, balance +/-1.2 to 1.8 points (see the backtest for what the
refits of short stretches actually did). The
fit can't see ride-height-dependent aero: it assumes ClA is constant, and it reads higher on a run's
faster half than its slower half (see README, Backtests), so quote ClA as an average over the run.

Checked on 26 MFT02 runs: mass 305-311 kg (the .ldx says 307), static front
weight 44-46% (.ldx "Vehicle COG" 0.45), effective CG height 243-267 mm (logged
CG height ~250), ClA 3.8-4.0 m², aero balance 32-35% front. On the Ford Mustang
Supercar: 1411-1420 kg vs 1410 - so it's not tuned to one car.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel

from .ingest.motec_ld import Channel, LdSession

EST = " (est.)"  # added to every published aero channel name so nobody mistakes it for a measurement
APPROX_NOTE = ("Approximation: AC doesn't log aero loads. These are estimated from the tyre loads "
               "with a fit to each run (see fs_telemetry/aero.py), so treat them as indicative, not measured.")

G = 9.81
FREQ = 30  # tyre loads are logged at 30 Hz
SMOOTH_S = 0.25  # aero doesn't change in a quarter of a second; smooths out bump noise
# Error bars. The fit's own standard error assumes every sample is independent, but neighbouring
# 30 Hz samples aren't (a bump or a corner lasts many samples), so it is too small. The backtest
# (fs-telemetry validate) refits short stretches of real runs and compares with the full-run answer:
# the real error was a few times the standard error for ClA (most of all on the Mustang) and about
# twice it for the balance. These factors make the stated +/- cover about 4 in 5 (ClA) and 9 in 10
# (balance) of those refits; the aero_fit backtest checks that they still do.
CLA_ERROR_FACTOR = 6.0
BALANCE_ERROR_FACTOR = 3.0
MAX_TRUSTED_CLA_ERROR_PCT = 8.0  # a run whose ClA could be off by more than this is flagged "Doubtful"
MIN_AERO_SPEED = 15.0  # m/s (54 kph): below this downforce is too small to split reliably
CORNERS = ("FL", "FR", "RL", "RR")
NEEDED = [*(f"Tire Load {c}" for c in CORNERS), "CG Accel Vertical", "CG Accel Longitudinal", "Ground Speed", "Air Density"]


class AeroCalibration(BaseModel):
    weight_n: float  # car + driver + fuel, from the fit
    mass_kg: float
    static_front_pct: float  # weight on the front axle at rest
    coefficient_m2: float  # ClA for the run (from the fit)
    balance_front_pct: float  # ClA_front / ClA
    cg_height_m: float | None  # effective CG height (needs the wheelbase from the .ldx)
    fit_error_n: float  # typical leftover load after the fit; small = trustworthy
    cla_error_pct: float  # how far ClA could be off, % (+/-), allowing for the fit's own uncertainty (see CLA_ERROR_FACTOR)
    balance_error_pp: float  # how far the aero balance could be off, percentage points (+/-)
    samples: int
    trustworthy: bool  # False if the fit error is large compared to the car's weight
    note: str


@dataclass
class _Signals:
    t: np.ndarray
    v: np.ndarray  # m/s
    az: np.ndarray  # G
    ax: np.ndarray  # G
    rho: np.ndarray  # kg/m³
    total: np.ndarray  # N, all four tyres
    front: np.ndarray  # N, front pair


def _signals(s: LdSession) -> _Signals | None:
    if not all(n in s.channels for n in NEEDED):
        return None
    t = np.arange(int(s.duration * FREQ)) / FREQ
    on = lambda n: np.interp(t, s.channels[n].time, s.channels[n].data)
    loads = {c: on(f"Tire Load {c}") for c in CORNERS}
    return _Signals(t=t, v=on("Ground Speed") / 3.6, az=on("CG Accel Vertical"), ax=on("CG Accel Longitudinal"),
                    rho=on("Air Density"), total=sum(loads.values()), front=loads["FL"] + loads["FR"])


def _robust_fit(A: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Least squares, then refit without samples > 3 standard deviations off (twice).
    Returns the coefficients, the leftover errors, and each coefficient's standard error
    (the textbook "how well do these samples pin it down", from the leftovers and the spread of the inputs)."""
    keep = np.ones(len(y), bool)
    for _ in range(3):
        if keep.sum() <= A.shape[1] or np.linalg.matrix_rank(A[keep]) < A.shape[1]:
            raise ValueError("Too little independent variation for an aero fit")
        coef, *_ = np.linalg.lstsq(A[keep], y[keep], rcond=None)
        resid = y - A @ coef
        spread = resid[keep].std()
        if spread <= np.finfo(float).eps * max(1.0, np.max(np.abs(y))):
            break
        next_keep = np.abs(resid) <= 3 * spread
        if np.array_equal(next_keep, keep):
            break
        keep = next_keep
    if keep.sum() <= A.shape[1] or np.linalg.matrix_rank(A[keep]) < A.shape[1]:
        raise ValueError("Too little independent variation for an aero fit")
    coef, *_ = np.linalg.lstsq(A[keep], y[keep], rcond=None)
    resid = y - A @ coef
    inverse = np.linalg.pinv(A[keep])
    se = resid[keep].std() * np.sqrt(np.sum(inverse**2, axis=1))
    return coef, resid[keep], se


def straight_running(s: LdSession, x: _Signals) -> np.ndarray:
    """The samples the fit uses: moving, nearly straight, not bouncing hard, tyres actually loaded."""
    lat = np.interp(x.t, s.channels["CG Accel Lateral"].time, s.channels["CG Accel Lateral"].data) \
        if "CG Accel Lateral" in s.channels else np.zeros_like(x.t)
    return (x.v > 3) & (np.abs(lat) < 0.3) & (np.abs(x.az) < 0.3) & (x.total > 100)


def model_loads(cal: AeroCalibration, k: float, x: _Signals) -> tuple[np.ndarray, np.ndarray]:
    """The tyre loads (total, front pair) the fitted equations above predict for each sample."""
    W, Wf = cal.weight_n, cal.weight_n * cal.static_front_pct / 100
    cla, cla_f = cal.coefficient_m2, cal.coefficient_m2 * cal.balance_front_pct / 100
    q = 0.5 * x.rho * x.v**2
    return W * (1 + x.az) + cla * q, Wf * (1 + x.az) + k * x.ax + cla_f * q


def calibrate(s: LdSession, x: _Signals | None = None,
              only: np.ndarray | None = None) -> tuple[AeroCalibration, float] | None:
    """Fit weight, front weight, transfer and ClA to the run. Returns (calibration, k).

    `only` (True/False per sample) limits the fit to part of the run - the backtests
    use it to fit on some laps and check the result on the others."""
    x = x or _signals(s)
    if x is None:
        return None
    use = straight_running(s, x)
    if only is not None:
        use &= only
    if use.sum() < 300:
        return None
    q = 0.5 * x.rho * x.v**2
    try:
        (W, cla), resid, (_, cla_se) = _robust_fit(np.c_[1 + x.az[use], q[use]], x.total[use])
        (Wf, k, cla_f), _, (_, _, cla_f_se) = _robust_fit(np.c_[1 + x.az[use], x.ax[use], q[use]], x.front[use])
    except (ValueError, np.linalg.LinAlgError):
        return None
    if not (100 < W < 50_000) or not (0 < Wf < W) or cla <= 0:
        return None
    try:
        L = float(s.details.get("Vehicle Wheelbase", "nan"))
    except ValueError:
        L = float("nan")
    h = -k * L / W if np.isfinite(L) else None
    error = float(resid.std())
    # Error bars: the fit's standard error, times a factor that makes them honest (see CLA_ERROR_FACTOR)
    cla_err = CLA_ERROR_FACTOR * cla_se / cla
    bal_err = BALANCE_ERROR_FACTOR * float(np.hypot(cla_f_se / cla, cla_f * cla_se / cla**2))
    well_pinned = 100 * cla_err <= MAX_TRUSTED_CLA_ERROR_PCT
    trustworthy = error < 0.1 * W and (h is None or 0.05 < h < 1.0) and well_pinned
    cal = AeroCalibration(
        weight_n=round(W, 1), mass_kg=round(W / G, 1), static_front_pct=round(100 * Wf / W, 2),
        coefficient_m2=round(cla, 3), balance_front_pct=round(100 * cla_f / cla, 2),
        cg_height_m=round(h, 4) if h is not None else None, fit_error_n=round(error, 1), samples=int(use.sum()),
        cla_error_pct=round(100 * cla_err, 1), balance_error_pp=round(100 * bal_err, 1),
        trustworthy=trustworthy,
        note="fitted to this run's straight-line running" + (
            "" if trustworthy else
            " - TOO LITTLE STRAIGHT-LINE RUNNING to pin the downforce down (short run?): treat ClA and balance with care"
            if error < 0.1 * W and not well_pinned else
            " - POOR FIT (big leftover loads, e.g. an off or a spin): treat aero numbers from this run with care"),
    )
    return cal, float(k)


def _smooth(a: np.ndarray) -> np.ndarray:
    n = max(1, int(SMOOTH_S * FREQ))
    return np.convolve(a, np.ones(n) / n, mode="same")


def aero_arrays(s: LdSession) -> tuple[AeroCalibration, dict[str, np.ndarray]] | None:
    """Downforce (total/front/rear), balance and coefficient on the 30 Hz clock."""
    x = _signals(s)
    result = calibrate(s, x) if x else None
    if result is None:
        return None
    cal, k = result
    W = cal.weight_n
    Wf = W * cal.static_front_pct / 100
    total = _smooth(x.total - W * (1 + x.az))
    front = _smooth(x.front - Wf * (1 + x.az) - k * x.ax)
    fast = x.v > MIN_AERO_SPEED
    q = 0.5 * x.rho * x.v**2
    return cal, {
        "Aero Downforce": total,
        "Aero Downforce Front": front,
        "Aero Downforce Rear": total - front,
        "Aero Balance": np.where(fast & (total > 0.1 * W), 100 * front / np.maximum(total, 1e-9), 0.0),
        "Downforce Coefficient": np.where(fast, total / np.maximum(q, 1e-9), 0.0),
    }


DESCRIPTIONS = {
    "Aero Downforce": "Total downforce: tyre loads minus the car's weight (corrected for bumps).",
    "Aero Downforce Front": "Front axle downforce, with braking/acceleration weight transfer taken out.",
    "Aero Downforce Rear": "Rear axle downforce (total minus front).",
    "Aero Balance": "Front share of downforce, % (above 54 kph; 0 below).",
    "Downforce Coefficient": "ClA = downforce / (½ · air density · speed²), m² (above 54 kph; 0 below).",
}
UNITS = {"Aero Downforce": "N", "Aero Downforce Front": "N", "Aero Downforce Rear": "N", "Aero Balance": "%",
         "Downforce Coefficient": "m2"}


def error_bar_text(cal: AeroCalibration) -> str:
    """The error bars in words, for channel descriptions (hover text) and the aero panel."""
    return (f"Error bars for this run: ClA ±{cal.cla_error_pct:.1f}%, balance ±{cal.balance_error_pp:.1f} points"
            f"{'' if cal.trustworthy else ' (doubtful fit)'}. Add up to ±3 points on balance if the drag acts well above or "
            "below the car's centre of gravity, and read ClA as an average over the run's speeds.")


def add_aero_channels(s: LdSession) -> None:
    result = aero_arrays(s)
    if result is None:
        return
    cal, arrays = result
    for key, values in arrays.items():
        name = key + EST
        if name not in s.channels:
            desc = (f"Approximation. {DESCRIPTIONS[key]} Estimated from tyre loads; calibrated from this run: "
                    f"{cal.mass_kg:.0f} kg, {cal.static_front_pct:.1f}% front. {error_bar_text(cal)}")
            s.channels[name] = Channel(name, UNITS[key], FREQ, np.nan_to_num(values), maths=True,
                                       approximate=True, description=desc)


# --- Aero map: the run summarised by speed band ---------------------------------


class AeroBand(BaseModel):
    speed_kph: str  # e.g. "90-108"
    samples: int
    downforce_n: float
    coefficient_m2: float
    balance_front_pct: float | None
    ride_height_front_mm: float | None
    ride_height_rear_mm: float | None


class AeroMap(BaseModel):
    approximate: bool = True  # always: these are estimates, not measurements
    note: str = APPROX_NOTE
    calibration: AeroCalibration
    downforce_at_100kph_n: float  # from the fitted ClA, at standard air density (1.225 kg/m³)
    bands: list[AeroBand]


def aero_map(s: LdSession, band_kph: float = 18.0) -> AeroMap | None:
    """Steady running only (little braking, cornering or bumping), banded by speed."""
    x = _signals(s)
    result = aero_arrays(s) if x else None
    if result is None:
        return None
    cal, a = result
    lat = np.interp(x.t, s.channels["CG Accel Lateral"].time, s.channels["CG Accel Lateral"].data) \
        if "CG Accel Lateral" in s.channels else np.zeros_like(x.t)
    steady = (np.abs(x.ax) < 0.15) & (np.abs(lat) < 0.3) & (np.abs(x.az) < 0.2)
    rh = {k: np.interp(x.t, s.channels[f"Ride Height {k}"].time, s.channels[f"Ride Height {k}"].data)
          for k in ("Front", "Rear") if f"Ride Height {k}" in s.channels}
    med = lambda arr, m: round(float(np.median(arr[m])), 2) if m.any() else None
    kph = x.v * 3.6
    bands = []
    for lo in np.arange(0, kph.max() + band_kph, band_kph):
        m = steady & (kph >= lo) & (kph < lo + band_kph) & (x.v > MIN_AERO_SPEED)
        if m.sum() < 20:
            continue
        bands.append(AeroBand(
            speed_kph=f"{lo:.0f}-{lo + band_kph:.0f}", samples=int(m.sum()),
            downforce_n=med(a["Aero Downforce"], m), coefficient_m2=med(a["Downforce Coefficient"], m),
            balance_front_pct=med(a["Aero Balance"], m) if "Aero Balance" in a else None,
            ride_height_front_mm=med(rh["Front"], m) if "Front" in rh else None,
            ride_height_rear_mm=med(rh["Rear"], m) if "Rear" in rh else None,
        ))
    return AeroMap(
        calibration=cal,
        downforce_at_100kph_n=round(0.5 * 1.225 * (100 / 3.6) ** 2 * cal.coefficient_m2, 0),
        bands=bands,
    )
