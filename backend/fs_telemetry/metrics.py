"""Engineering metrics: corner gating, balance, G-G and driver inputs.

Corner gating sorts every metre of a lap into one bucket ("gate"), checked in
this order - the first rule that matches wins:

    Braking        brake pedal above brake_pct
    Power limited  throttle >= power_throttle_pct  OR/AND  |lateral G| < power_lat_g
    LS / MS / HS   whatever's left (i.e. cornering), split by speed:
                   below ls_max_kph / below ms_max_kph / above

Every limit is a setting, so the team can tune the definitions to the car and
track. The gates are worked out on the REFERENCE lap (the first one) and then
applied to every lap by distance - so "time in LS corners" is measured over the
same bits of track for every lap, and the laps can be compared fairly.

Balance = average |slip angle| of the fronts minus the rears, in degrees, only
while cornering at least balance_min_lat_g:
    positive -> fronts slipping more -> understeer
    negative -> rears slipping more  -> oversteer
Files without slip angles (iRacing doesn't log them) use the best of these
from maths.py instead, same signs:
    Understeer Angle     front wheel angle beyond what the corner needs, from
                         the car's known steering ratio (+ wheelbase if known)
    Steering Understeer  steering-wheel angle beyond what the corner needs,
                         all learned from the run (a different scale)
The response says which was used and what it rests on (known or learned).

An average hides short moments of oversteer (a slide on exit, rotation on
entry), as mid-corner understeer outweighs them. So each gate and lap also
gets the share of cornering time spent oversteering (balance below 0) and the
5th percentile: the balance the most oversteery 5% of cornering is beyond.
The steering-based measures read exactly 0 where they can't measure (barely
turning, crawling); those points are left out of all three numbers.

Mechanical balance (ESTIMATED; needs tyre loads, so AC only - iRacing doesn't
log them) is the front share of the load moving across the car in steady
cornering (lateral load transfer distribution, LLTD; see maths.load_transfer
for which samples count). Per gate and lap it's total front transfer / total
transfer, so moments with more load moving count more. Its spread is how much
it varies corner to corner (a "corner" is an unbroken stretch above
balance_min_lat_g): the standard deviation, weighted the same way. Plus the share in
bands of lateral G: flat means the springs and bars behave linearly; a trend
means something non-linear (bump stops, packers, a tyre lifting).
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator

from .accuracy import balance_statement
from .catalog import Catalog
from .compare import CompareError, LapRef, load_timed_laps
from .laps import resample_lap
from .maths import load_transfer

GATES = ["Braking", "LS", "MS", "HS", "Power limited"]
BRAKING, LS, MS, HS, POWER = range(len(GATES))
STEP_M = 2.0
NEEDED = ["Ground Speed", "Throttle Pos", "Brake Pos", "CG Accel Lateral", "CG Accel Longitudinal"]
SLIP_ANGLES = ["Tire Slip Angle FL", "Tire Slip Angle FR", "Tire Slip Angle RL", "Tire Slip Angle RR"]
TYRE_LOADS = ["Tire Load FL", "Tire Load FR", "Tire Load RL", "Tire Load RR"]  # for mechanical balance (AC)
MECH_MIN_POINTS = 10  # 20 m of steady cornering (on the STEP_M grid) before a mech balance is given
MECH_MIN_CORNER_POINTS = 5  # a corner needs 10 m of steady cornering to count towards the spread
MECH_BAND_G = 0.25  # width of the lateral G bands for mech balance vs lateral G
# The ways balance can be measured, best first: (channel, what it is, chart axis title)
BALANCE_MEASURES = {
    "slip": (None, "front − rear slip angle", "Front − rear slip angle [°]"),
    "understeer": ("Understeer Angle", "front wheel angle beyond what the corner needs", "Extra front wheel angle [°]"),
    "steering": ("Steering Understeer", "steering-wheel angle beyond what the corner needs", "Extra steering [°]"),
}
OPTIONAL = ["Car Coord X", "Car Coord Y", *SLIP_ANGLES, "Understeer Angle", "Steering Understeer",
            *TYRE_LOADS]  # used when the file has them


class MetricSettings(BaseModel):
    """Every definition the metrics use. Sent as query parameters; all optional."""

    ls_max_kph: float = Field(80, gt=0, description="Cornering below this speed is LS")
    ms_max_kph: float = Field(160, gt=0, description="Cornering below this speed (and above LS) is MS; above is HS")
    brake_pct: float = Field(5, ge=0, le=100, description="Brake pedal above this % counts as Braking")
    power_throttle_pct: float = Field(98, ge=0, le=100, description="Throttle at or above this % can count as Power limited")
    power_lat_g: float = Field(0.5, ge=0, description="|Lateral G| below this can count as Power limited")
    power_rule: Literal["or", "and"] = Field("or", description="'or': either condition; 'and': flat out AND nearly straight")
    balance_min_lat_g: float = Field(0.5, ge=0, description="Only measure balance while cornering at least this hard")
    full_throttle_pct: float = Field(98, ge=0, le=100, description="Throttle at or above this % is 'full throttle'")
    pedal_on_pct: float = Field(2, ge=0, le=100, description="A pedal above this % counts as pressed")

    @model_validator(mode="after")
    def _bands_in_order(self):
        if self.ms_max_kph <= self.ls_max_kph:
            raise ValueError("MS limit must be above the LS limit")
        return self


class GateStats(BaseModel):
    gate: str
    distance_pct: float  # share of the lap's distance
    time_s: float  # time spent in this gate
    avg_speed_kph: float | None
    avg_combined_g: float | None  # same as g_abs_avg (kept so older pages still work)
    g_abs_avg: float | None  # G absolute = sqrt(lat² + long²): how hard the tyres are working, on average
    g_abs_p95: float | None  # ...and near its peak: 95th percentile (a true maximum is one spike)
    balance_deg: float | None  # front minus rear slip angle; + understeer, - oversteer
    oversteer_pct: float | None  # share of cornering time with balance below 0
    balance_p5_deg: float | None  # worst 5% of cornering: the most oversteery end of the balance
    mech_balance_pct: float | None  # est. front share of lateral load transfer, steady cornering (tyre loads: AC only)
    mech_balance_spread: float | None  # how much that varies corner to corner (std, % points); None under 3 corners


class LapSummary(BaseModel):
    balance_deg: float | None
    oversteer_pct: float | None
    balance_p5_deg: float | None
    mech_balance_pct: float | None
    mech_balance_spread: float | None
    mech_corners: int  # stretches of steady cornering the mech balance comes from
    avg_combined_g: float  # same as g_abs_avg (kept so older pages still work)
    g_abs_avg: float
    g_abs_p95: float
    max_lat_g: float
    max_brake_g: float
    full_throttle_pct: float
    braking_pct: float
    coasting_pct: float
    overlap_pct: float


class GG(BaseModel):
    """Points for the G-G diagram, one every STEP_M metres."""

    lat: list[float]
    long: list[float]
    gate: list[int]  # index into Metrics.gates
    speed: list[float]


class MechBand(BaseModel):
    """Mech balance in one band of lateral G (est.)."""

    lat_g: float  # middle of the band
    front_pct: float
    points: int  # how many STEP_M points of steady cornering it rests on


class LapMetricsOut(BaseModel):
    session_id: str
    lap: int
    driver: str
    lap_time_s: float
    summary: LapSummary
    gates: list[GateStats]
    gg: GG
    balance_basis: str | None  # what this lap's balance rests on, e.g. "logged slip angles" or "known steering ratio..."
    balance_accuracy: str | None = None  # how far to trust it, from the backtest (accuracy.py)
    mech_vs_lat_g: list[MechBand]  # empty without tyre loads (iRacing)


class GateMap(BaseModel):
    """The reference lap's line with each point's gate - shows what the settings pick out."""

    x: list[float]
    y: list[float]
    distance: list[float]
    gate: list[int]


class Metrics(BaseModel):
    settings: MetricSettings
    gates: list[str]
    gate_map: GateMap
    laps: list[LapMetricsOut]
    balance_label: str  # what the balance numbers measure and rest on (known or learned); "" if none
    balance_axis: str  # short title for the balance chart's axis
    balance_accuracy: str = ""  # how far to trust the balance numbers, from the backtest (accuracy.py)


def classify(speed, throttle, brake, lat_g, st: MetricSettings) -> np.ndarray:
    """Gate number for every sample (see module docstring for the rules)."""
    flat = throttle >= st.power_throttle_pct
    straight = np.abs(lat_g) < st.power_lat_g
    power = (flat | straight) if st.power_rule == "or" else (flat & straight)
    band = np.where(speed < st.ls_max_kph, LS, np.where(speed < st.ms_max_kph, MS, HS))
    return np.where(brake > st.brake_pct, BRAKING, np.where(power, POWER, band))


def balance_measure(r: dict) -> str | None:
    """The best balance this lap's data allows (a key of BALANCE_MEASURES), or None."""
    if all(c in r for c in SLIP_ANGLES):
        return "slip"
    return next((m for m, (channel, _, _) in BALANCE_MEASURES.items() if channel and channel in r), None)


def balance_basis(s, measure: str | None) -> str | None:
    """What the balance rests on, to show next to it: logged, or the maths channel's basis."""
    if measure is None:
        return None
    channel = BALANCE_MEASURES[measure][0]
    return "logged slip angles" if channel is None else s.channels[channel].basis


def balance(r: dict) -> np.ndarray | None:
    measure = balance_measure(r)
    if measure is None:
        return None
    if measure != "slip":
        return r[BALANCE_MEASURES[measure][0]]
    front = (np.abs(r["Tire Slip Angle FL"]) + np.abs(r["Tire Slip Angle FR"])) / 2
    rear = (np.abs(r["Tire Slip Angle RL"]) + np.abs(r["Tire Slip Angle RR"])) / 2
    return front - rear


def _mean(values: np.ndarray, digits: int = 3) -> float | None:
    return round(float(values.mean()), digits) if len(values) else None


def _p95(values: np.ndarray) -> float | None:
    return round(float(np.percentile(values, 95)), 3) if len(values) else None


def _front_share(front: np.ndarray, total: np.ndarray) -> float:
    """Front share of the load moving across, % - weighted by how much is moving."""
    return 100.0 * float(np.sum(front * np.sign(total)) / np.sum(np.abs(total)))


def corner_numbers(cornering: np.ndarray) -> np.ndarray:
    """Number each unbroken stretch of cornering 1, 2, 3... along the lap (0 = not cornering)."""
    return np.cumsum(cornering & ~np.r_[False, cornering[:-1]]) * cornering


def mech_stats(transfer, where: np.ndarray, corners: np.ndarray) -> dict:
    """Mech balance where `where` is true, with its corner-to-corner spread (see the top of this file).
    corners: corner_numbers() of the lap. A corner needs MECH_MIN_CORNER_POINTS of steady
    cornering to count towards the spread (it still counts towards the balance)."""
    none = {"mech_balance_pct": None, "mech_balance_spread": None, "corners": 0}
    if transfer is None:
        return none
    front, total, steady = transfer
    m = where & steady
    if m.sum() < MECH_MIN_POINTS:
        return none
    shares, weights = [], []
    for k in np.unique(corners[m]):
        c = m & (corners == k)
        if c.sum() >= MECH_MIN_CORNER_POINTS:
            shares.append(_front_share(front[c], total[c]))
            weights.append(np.sum(np.abs(total[c])))
    pct = _front_share(front[m], total[m])
    spread = (float(np.sqrt(np.average((np.array(shares) - pct) ** 2, weights=weights)))
              if len(shares) >= 3 else None)
    return {"mech_balance_pct": round(pct, 1), "mech_balance_spread": None if spread is None else round(spread, 1),
            "corners": len(shares)}


def mech_vs_lat_g(transfer, lat: np.ndarray, min_lat_g: float) -> list[MechBand]:
    """Mech balance in MECH_BAND_G bands of |lateral G|, from min_lat_g up; bands with too little are skipped."""
    if transfer is None:
        return []
    front, total, steady = transfer
    g = np.abs(lat)
    bands = []
    for lo in np.arange(min_lat_g, g.max(initial=0), MECH_BAND_G):
        m = steady & (g >= lo) & (g < lo + MECH_BAND_G)
        if m.sum() >= MECH_MIN_POINTS:
            bands.append(MechBand(lat_g=round(float(lo + MECH_BAND_G / 2), 3),
                                  front_pct=round(_front_share(front[m], total[m]), 1), points=int(m.sum())))
    return bands


def balance_stats(bal: np.ndarray | None) -> dict:
    """Average balance, % of the time oversteering (below 0) and the worst 5% (5th percentile)."""
    if bal is None or not len(bal):
        return {"balance_deg": None, "oversteer_pct": None, "balance_p5_deg": None}
    return {"balance_deg": _mean(bal), "oversteer_pct": round(100.0 * float(np.mean(bal < 0)), 1),
            "balance_p5_deg": round(float(np.percentile(bal, 5)), 3)}


def _round(a) -> list[float]:
    return [round(float(v), 3) for v in a]


def lap_metrics(r: dict, gate: np.ndarray, st: MetricSettings, lap_time: float
                ) -> tuple[LapSummary, list[GateStats], list[MechBand]]:
    """r: one lap resampled on distance; gate: the gate of each of its points."""
    speed, thr, brk = r["Ground Speed"], r["Throttle Pos"], r["Brake Pos"]
    lat, lon = r["CG Accel Lateral"], r["CG Accel Longitudinal"]
    combined = np.hypot(lat, lon)
    bal = balance(r)
    transfer = load_transfer(r, st.balance_min_lat_g) if all(c in r for c in TYRE_LOADS) else None
    # Cornering hard enough to judge balance, and measured (see the top of this file about 0s)
    cornering = (np.abs(lat) >= st.balance_min_lat_g) & (bal != 0) if bal is not None else None
    # Time taken to cover each step, credited to the gate the step starts in. The
    # grid starts a metre or two after the line and stops just short of it, so
    # those bits go to the first and last points - then the gates add up to the lap.
    dt = np.append(np.diff(r["Time"]), lap_time - r["Time"][-1]) if len(r["Time"]) else np.array([])
    if len(dt):
        dt[0] += r["Time"][0]
    n = len(speed)
    pct = lambda mask: round(100.0 * float(np.mean(mask)), 1) if n else 0.0

    corners = corner_numbers(np.abs(lat) >= st.balance_min_lat_g)

    gates = []
    for g, name in enumerate(GATES):
        m = gate == g
        mech = mech_stats(transfer, m, corners)
        gates.append(GateStats(
            gate=name, distance_pct=pct(m), time_s=round(float(dt[m].sum()), 3),
            avg_speed_kph=_mean(speed[m], 1), avg_combined_g=_mean(combined[m]),
            g_abs_avg=_mean(combined[m]), g_abs_p95=_p95(combined[m]),
            **balance_stats(bal[m & cornering] if bal is not None else None),
            mech_balance_pct=mech["mech_balance_pct"], mech_balance_spread=mech["mech_balance_spread"],
        ))
    mech = mech_stats(transfer, np.ones(n, bool), corners)
    summary = LapSummary(
        **balance_stats(bal[cornering] if bal is not None else None),
        mech_balance_pct=mech["mech_balance_pct"], mech_balance_spread=mech["mech_balance_spread"],
        mech_corners=mech["corners"],
        avg_combined_g=round(float(combined.mean()), 3) if n else 0.0,
        g_abs_avg=round(float(combined.mean()), 3) if n else 0.0,
        g_abs_p95=_p95(combined) or 0.0,
        max_lat_g=round(float(np.abs(lat).max(initial=0)), 3),
        max_brake_g=round(float(-lon.min(initial=0)), 3),
        full_throttle_pct=pct(thr >= st.full_throttle_pct),
        braking_pct=pct(brk > st.pedal_on_pct),
        coasting_pct=pct((thr < st.pedal_on_pct) & (brk <= st.pedal_on_pct)),
        overlap_pct=pct((thr > st.pedal_on_pct) & (brk > st.pedal_on_pct)),
    )
    return summary, gates, mech_vs_lat_g(transfer, lat, st.balance_min_lat_g)


def compute_metrics(catalog: Catalog, refs: list[LapRef], st: MetricSettings) -> Metrics:
    loaded = load_timed_laps(catalog, refs)
    for _, s, _ in loaded:
        missing = [c for c in NEEDED if c not in s.channels]
        if missing:
            raise CompareError(f"{s.driver}'s session has no {', '.join(missing)} channel(s)")
    resampled = [resample_lap(s, ref.lap, NEEDED + [c for c in OPTIONAL if c in s.channels], STEP_M)
                 for ref, s, _ in loaded]

    # Gates from the reference lap, by distance
    ref = resampled[0]
    ref_gate = classify(ref["Ground Speed"], ref["Throttle Pos"], ref["Brake Pos"], ref["CG Accel Lateral"], st)

    def gate_at(distance: np.ndarray) -> np.ndarray:
        k = np.clip(np.searchsorted(ref["Distance"], distance), 0, len(ref_gate) - 1)
        return ref_gate[k]

    laps, used, accuracy = [], {}, {}  # used: (measure, basis) -> None, in order, so each is described once
    for (lap_ref, s, info), r in zip(loaded, resampled):
        g = gate_at(r["Distance"])
        summary, gates, mech_curve = lap_metrics(r, g, st, info.lap_time)
        measure = balance_measure(r)
        basis = balance_basis(s, measure)
        how_far = balance_statement(measure, s.car)
        if measure:
            used[(measure, basis)] = None
        if how_far:
            accuracy[how_far] = None
        laps.append(LapMetricsOut(
            session_id=lap_ref.session_id, lap=lap_ref.lap, driver=s.driver, lap_time_s=round(info.lap_time, 3),
            summary=summary, gates=gates, balance_basis=basis, balance_accuracy=how_far, mech_vs_lat_g=mech_curve,
            gg=GG(lat=_round(r["CG Accel Lateral"]), long=_round(r["CG Accel Longitudinal"]),
                  gate=[int(v) for v in g], speed=_round(r["Ground Speed"])),
        ))

    # e.g. "front wheel angle beyond what the corner needs — known steering ratio 12:1 (cars.py) and ..."
    label = " / ".join(f"{BALANCE_MEASURES[m][1]} — {b}" for m, b in used)
    axes = {BALANCE_MEASURES[m][2] for m, _ in used}
    return Metrics(
        settings=st, gates=GATES, laps=laps,
        balance_label=f"{label} · + understeer · − oversteer" if label else "",
        balance_axis=axes.pop() if len(axes) == 1 else "Balance [°]",
        balance_accuracy=" · ".join(accuracy),
        gate_map=GateMap(x=_round(ref.get("Car Coord X", [])), y=_round(ref.get("Car Coord Y", [])),
                         distance=_round(ref["Distance"]), gate=[int(v) for v in ref_gate]),
    )
