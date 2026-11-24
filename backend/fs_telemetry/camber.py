"""Optimal static camber: the camber the car really ran, scored against the tyre's own camber curve.

AC logs each wheel's camber to the road ("Camber FL", degrees, - = top of the
wheel leaning in, the same sign on both sides). It already includes everything
the suspension does: static camber, camber gain in bump and droop, and body
roll. Checked on MFT02 runs: a loaded (outside) wheel loses negative camber as
the car rolls, so camber rises with tyre load.

The tyre's grip vs camber comes from the car's tyres.ini, per axle and compound:
    DCAMBER_0, DCAMBER_1   grip multiplier  f(γ) = 1 + DCAMBER_0·|γ| + DCAMBER_1·γ²
                           (γ in radians), which peaks at |γ*| = DCAMBER_0 / (2·|DCAMBER_1|)
                           with a grip gain of DCAMBER_0² / (4·|DCAMBER_1|) over zero camber.
    ASSUMPTION: Kunos' comment in tyres.ini writes it as D·(1 - (...)), which taken
    literally has no peak at all. The parabola above is the usual reading and matches
    how AC cars respond, but confirm it with a back-to-back skidpad test (two
    camber settings) before trusting the exact number.

Optimising: changing static camber by δ shifts every sample's camber by about δ
(camber gain barely depends on the static setting). For each axle and each δ
in the search range, the score is the load-weighted grip the camber allows
while the car is near the limit:
    score(δ) = Σ w · f(γ(t) + δ)  /  Σ w · f(γ*)       (100% = always at the peak)
with w = Fz^LS_EXPY (tyres.ini; about 0.73-0.8): a tyre's force grows slower than its
load, so weighting by Fz itself over-counts the heavily loaded outside tyre.
Over the samples near the limit: G at least min_g (and at most max_g, if given) ×
the selection's peak G. The G is lateral only (g_basis "auto"; the Braking gate uses
combined G, since braking has little lateral G). The peak is a percentile of it over
the selection's whole laps (peak_pct, default the 98th, ignoring one-sample spikes
from kerbs). The outside tyre counts most, as it does most of the cornering; the
inside tyre pulls the answer back.

Because f is a parabola, the best δ is simply "peak camber minus the weighted mean
running camber". The score is very flat near the peak (a few tenths of a point
over a degree or more), so an axle's change is only called worthwhile if it gains
min_gain_pct points; smaller gains are within what this model can tell apart.
Tread temperatures (inner minus outer) are a cross-check, not part of the score:
a change that adds to an already hot inner (or outer) tread is flagged. Camber
logged on straights (calm_deg) is shown so it can be compared with the setup: it
includes ride height, so it needn't equal the static camber typed in the setup.

Gating, to look at one part of the corner (e.g. grip trouble on entry, away
from peak lateral G, or a track whose grip changed):
    gate     only the parts of the lap in one Metrics gate (Braking / LS / MS /
             HS / Power limited), from the reference lap by distance, exactly
             as the damper histograms do (dampers.gate_filter)
    max_g    an upper G limit, so e.g. 0.4-0.8 × peak looks at the build-up and
             release of the corner rather than its apex
    peak_pct which percentile counts as "peak". The peak always comes from the
             whole laps, so the G limits mean the same thing in every gate.

Kunos' curve uses the size of the camber, not its sign, so positive camber would
score as well as negative. Changes that would leave an axle's load-weighted
average camber positive aren't tried: no race car wants it. That also matters for
the inside tyre, which roll can push to positive camber: with inside_tyre "abs"
(default, the tyres.ini comment) it still earns grip; with "signed" positive camber
loses grip on the same parabola, as Camber Extravaganza treats it. Which one AC
really does isn't known: compare the two answers, and test on track.

Not scored: tyre temperatures (beyond the cross-check) and wear, AC's thermal tyre
model, camber thrust (CAMBER_GAIN) and the small change in camber gain with static
camber. On the Tatuus the rear runs best well below the curve's peak, which these
effects, not the grip curve, are the likely reason for. iRacing logs no camber, so
this is AC only.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator

from .cars import ac_data_dir, read_ini
from .catalog import Catalog
from .compare import CompareError, LapRef
from .dampers import _selections, gate_filter
from .ingest.motec_ld import LdSession
from .maths import CORNERS
from .metrics import MetricSettings

AXLES = {"Front": ("FL", "FR"), "Rear": ("RL", "RR")}
CURVE_DEG = np.round(np.arange(-8.0, 0.01, 0.1), 2)  # angles the tyre curve is drawn over
CALM_G = 0.12  # combined G below which the car is "calm" (straights, pit lane): camber there is close to the setup's static camber


class CamberSettings(BaseModel):
    """What to count and which tyre curve to use. Sent as query parameters; all optional."""

    scope: Literal["lap", "run"] = Field("run", description="'lap': each compared lap; 'run': every timed lap of each lap's run")
    gate: Literal["all", "Braking", "LS", "MS", "HS", "Power limited"] = Field("all", description="Only count the parts of the lap in this Metrics gate")
    peak_pct: float = Field(98, ge=50, le=100, description="This percentile of combined G over the whole laps is the peak")
    min_g: float = Field(0.6, ge=0, le=1, description="Only count samples using at least this share of the peak combined G")
    max_g: float | None = Field(None, gt=0, le=2, description="...and at most this share (no upper limit if not given)")
    span_deg: float = Field(3.0, gt=0, le=6, description="Try static camber changes up to this far either way")
    step_deg: float = Field(0.1, ge=0.05, le=1, description="Step between the changes tried")
    inside_tyre: Literal["abs", "signed"] = Field(
        "abs", description="'abs': grip depends on the size of the camber only (the tyres.ini comment), so a tyre pushed to positive camber "
                           "by roll still earns grip; 'signed': positive camber (top out) loses grip, which penalises the inside tyre")
    g_basis: Literal["auto", "lateral", "combined"] = Field(
        "auto", description="G the limits are shares of: 'lateral' = cornering only, 'combined' = lateral and braking/traction; "
                            "'auto' is lateral, except in the Braking gate (which has little lateral G)")
    min_gain_pct: float = Field(0.5, ge=0, le=5, description="An axle's best change only counts as worthwhile if it scores at least this many points above the current camber")
    ls_expy: float | None = Field(None, gt=0.2, le=1.5, description="Load-sensitivity exponent (tyre force ~ load^this) for both axles, instead of LS_EXPY from tyres.ini")
    compound: int | None = Field(None, ge=0, description="tyres.ini compound index; the car's default if not given")
    front_dcamber0: float | None = Field(None, description="Use this DCAMBER_0 for the front instead of tyres.ini")
    front_dcamber1: float | None = Field(None, lt=0, description="...and this DCAMBER_1 (negative)")
    rear_dcamber0: float | None = Field(None, description="Use this DCAMBER_0 for the rear instead of tyres.ini")
    rear_dcamber1: float | None = Field(None, lt=0, description="...and this DCAMBER_1 (negative)")

    @model_validator(mode="after")
    def _pairs(self):
        if self.max_g is not None and self.max_g <= self.min_g:
            raise ValueError("The upper G limit must be above the lower one")
        for axle in ("front", "rear"):
            if (getattr(self, f"{axle}_dcamber0") is None) != (getattr(self, f"{axle}_dcamber1") is None):
                raise ValueError(f"Give both {axle} DCAMBER_0 and DCAMBER_1, or neither")
        return self


class TyreCurve(BaseModel):
    axle: str
    dcamber0: float
    dcamber1: float
    peak_deg: float  # camber with the most grip (negative)
    peak_gain_pct: float  # grip at the peak over grip at zero camber
    source: str  # where the numbers came from, in words
    grip_pct: list[float]  # f at each CURVE_DEG angle, as % of the peak
    ls_expy: float = 1.0  # tyre force goes with load to this power (LS_EXPY): how much the loaded tyre counts
    signed: bool = False  # grip falls off on the positive-camber side too (see CamberSettings.inside_tyre)


class CornerCamber(BaseModel):
    corner: str
    mean_deg: float | None  # weighted average camber while working
    p10_deg: float | None
    p90_deg: float | None
    calm_deg: float | None = None  # median camber where G is near zero (straights): compare with the setup's static camber
    spread_c: float | None = None  # weighted mean inner minus outer tread temperature while working (+ = inner hotter)


class AxleResult(BaseModel):
    axle: str
    gain_pct: float | None = None  # best score minus the current score, in points
    worthwhile: bool = False  # the gain reaches min_gain_pct: below that the score is too flat to trust the answer
    spread_c: float | None = None  # weighted inner minus outer tread temperature over both tyres of the axle
    temp_conflict: bool = False  # the tread temperatures argue against the change
    temp_note: str | None = None
    offsets_deg: list[float]  # static camber changes tried (- = more negative camber)
    score_pct: list[float | None]  # score at each change (None: not tried, it would make the camber positive)
    current_pct: float | None  # score as run (change 0)
    best_offset_deg: float | None
    best_pct: float | None
    at_limit: bool  # the best change is at the end of the range tried: the real best may be further


class Selection(BaseModel):
    session_id: str
    lap: int | None  # None for a whole run
    laps: list[int]
    driver: str
    car: str
    time_s: float  # time counted (near the limit, in the gate)
    peak_g: float  # the peak G the limits are shares of
    g_basis: str = "combined"  # which G that is: 'lateral' or 'combined'
    compound: str
    compounds: list[str]  # the car's compounds, by index
    tyres: list[TyreCurve]
    corners: list[CornerCamber]
    axles: list[AxleResult]


class Camber(BaseModel):
    settings: CamberSettings
    curve_deg: list[float]
    selections: list[Selection]
    note: str


# --- The tyre ---------------------------------------------------------------------


def camber_grip(camber_deg, d0: float, d1: float, signed: bool = False):
    """Grip multiplier for camber (degrees), on the parabola in the module docstring. Never below 0.
    signed: camber with the top out (positive) counts as negative inclination, so it loses grip; otherwise only
    the size of the camber matters, as in the tyres.ini comment."""
    g = np.radians(-np.asarray(camber_deg, dtype=float)) if signed else np.abs(np.radians(camber_deg))
    return np.maximum(1.0 + d0 * g + d1 * g * g, 0.0)


def peak_camber_deg(d0: float, d1: float) -> float:
    """The camber with the most grip, as a negative angle."""
    return -math.degrees(d0 / (2.0 * abs(d1)))


def tyre_curve(axle: str, d0: float, d1: float, source: str, ls_expy: float = 1.0, signed: bool = False) -> TyreCurve:
    if d1 >= 0:
        raise CompareError(f"The {axle.lower()} tyre's DCAMBER_1 is {d1}: it has to be negative for the curve to have a peak")
    peak = peak_camber_deg(d0, d1)
    top = float(camber_grip(peak, d0, d1))
    return TyreCurve(axle=axle, dcamber0=d0, dcamber1=d1, peak_deg=round(peak, 2),
                     peak_gain_pct=round(100.0 * (top - 1.0), 2), source=source, ls_expy=ls_expy, signed=signed,
                     grip_pct=[round(float(v), 3) for v in 100.0 * camber_grip(CURVE_DEG, d0, d1) / top])


def compounds_in(ini: dict[str, dict[str, str]]) -> list[str]:
    names, i = [], 0
    while (front := ini.get("FRONT" if i == 0 else f"FRONT_{i}")) is not None:
        names.append(front.get("NAME") or front.get("SHORT_NAME") or f"Compound {i}")
        i += 1
    return names


def tyres_for(car: str, st: CamberSettings, data_dir: Path | None = None) -> tuple[str, list[str], list[TyreCurve]]:
    """(compound name, all compound names, front and rear curves) for a car: typed-in numbers first, then tyres.ini."""
    signed = st.inside_tyre == "signed"
    typed = {a: (getattr(st, f"{a.lower()}_dcamber0"), getattr(st, f"{a.lower()}_dcamber1")) for a in AXLES}
    if all(d0 is not None for d0, _ in typed.values()):
        return "typed in", [], [tyre_curve(a, d0, d1, "typed in", st.ls_expy or 1.0, signed) for a, (d0, d1) in typed.items()]

    data_dir = data_dir or ac_data_dir(car)
    path = data_dir / "tyres.ini" if data_dir else None
    if path is None or not path.is_file():
        raise CompareError(
            f"Can't find {car}'s tyres.ini. Kunos cars keep it packed in data.acd: unpack it with Content Manager "
            f"and copy the data folder to 'FS Telemetry\\cars\\{car}\\data', or type the DCAMBER numbers in.")
    ini = read_ini(path.read_text(errors="ignore"))
    names = compounds_in(ini)
    if not names:
        raise CompareError(f"{path} has no [FRONT] tyre section")
    default = ini.get("COMPOUND_DEFAULT", {}).get("INDEX", "0")
    i = st.compound if st.compound is not None else (int(default) if default.isdigit() else 0)
    if i >= len(names):
        raise CompareError(f"{car} has {len(names)} tyre compound(s); there's no compound {i}")
    curves = []
    for axle in AXLES:
        sec = ini.get(axle.upper() if i == 0 else f"{axle.upper()}_{i}", {})
        d0, d1 = typed[axle]
        try:
            ls = st.ls_expy or float(sec["LS_EXPY"])
        except (KeyError, ValueError):
            ls = 1.0  # no exponent in the file: count load linearly
        if d0 is not None:
            curves.append(tyre_curve(axle, d0, d1, "typed in", ls, signed))
            continue
        try:
            d0, d1 = float(sec["DCAMBER_0"]), float(sec["DCAMBER_1"])
        except (KeyError, ValueError):
            raise CompareError(f"{path} has no DCAMBER_0 / DCAMBER_1 for the {axle.lower()} {names[i]} tyre") from None
        curves.append(tyre_curve(axle, d0, d1, f"tyres.ini, {names[i]}", ls, signed))
    return names[i], names, curves


# --- The maths, on plain arrays (easy to test) -------------------------------------


def offsets(st: CamberSettings) -> np.ndarray:
    n = int(round(st.span_deg / st.step_deg))
    return np.round(np.arange(-n, n + 1) * st.step_deg, 3)


def axle_score(camber_deg: np.ndarray, load_n: np.ndarray, deltas: np.ndarray, curve: TyreCurve) -> np.ndarray:
    """Score (%) for each static camber change in deltas: load-weighted grip over load-weighted peak grip.
    NaN for changes that would make the load-weighted average camber positive (see the module docstring)."""
    w = tyre_weight(load_n, curve.ls_expy)
    if not w.sum():
        return np.full(len(deltas), np.nan)
    top = float(camber_grip(curve.peak_deg, curve.dcamber0, curve.dcamber1))
    grip = camber_grip(camber_deg[None, :] + deltas[:, None], curve.dcamber0, curve.dcamber1, curve.signed)
    score = 100.0 * (grip * w).sum(axis=1) / (w.sum() * top)
    return np.where(np.average(camber_deg, weights=w) + deltas > 0, np.nan, score)


def tyre_weight(load_n: np.ndarray, ls_expy: float) -> np.ndarray:
    """How much each sample counts: the tyre's force, which grows with load^LS_EXPY (below 1: a tyre that carries
    twice the load gives less than twice the force), not with the load itself."""
    return np.maximum(load_n, 0.0) ** ls_expy


# Tread temperature: inner minus outer, in °C, that a camber change is checked against. Race tyres are usually
# happiest with the inner tread a few degrees hotter than the outer: hotter than TEMP_INNER_MAX says there's
# already too much camber, outer hotter than inner (below TEMP_INNER_MIN) says too little.
TEMP_INNER_MIN, TEMP_INNER_MAX = 0.0, 10.0


def temp_check(offset_deg: float | None, spread_c: float | None) -> tuple[bool, str | None]:
    """(conflict, note): does the tread temperature argue against changing static camber by offset_deg?"""
    if spread_c is None or not offset_deg:
        return False, None
    if offset_deg < 0 and spread_c > TEMP_INNER_MAX:
        return True, f"Inner tread is already {spread_c:.0f} °C hotter than the outer: more negative camber would add to it"
    if offset_deg > 0 and spread_c < TEMP_INNER_MIN:
        return True, f"Outer tread is {-spread_c:.0f} °C hotter than the inner: less negative camber would add to it"
    return False, f"Tread temperatures agree (inner minus outer {spread_c:+.0f} °C)"


def axle_result(axle: str, camber: list[np.ndarray], load: list[np.ndarray], deltas: np.ndarray, curve: TyreCurve,
                min_gain_pct: float = 0.5, spread: list[np.ndarray] | None = None) -> AxleResult:
    score = axle_score(np.concatenate(camber), np.concatenate(load), deltas, curve)
    if np.isnan(score).all():
        return AxleResult(axle=axle, offsets_deg=deltas.tolist(), score_pct=[], current_pct=None,
                          best_offset_deg=None, best_pct=None, at_limit=False)
    best = int(np.nanargmax(score))
    tried = np.flatnonzero(~np.isnan(score))
    now = score[len(deltas) // 2]
    gain = None if np.isnan(now) else float(score[best] - now)
    w = tyre_weight(np.concatenate(load), curve.ls_expy)
    spread_c = None
    if spread is not None and w.sum():
        spread_c = round(float(np.average(np.concatenate(spread), weights=w)), 1)
    conflict, note = temp_check(float(deltas[best]), spread_c)
    return AxleResult(axle=axle, offsets_deg=deltas.tolist(),
                      score_pct=[None if np.isnan(v) else round(float(v), 3) for v in score],
                      current_pct=None if np.isnan(now) else round(float(now), 3), best_offset_deg=float(deltas[best]),
                      best_pct=round(float(score[best]), 3), at_limit=best in (tried[0], tried[-1]),
                      gain_pct=None if gain is None else round(gain, 3), worthwhile=gain is not None and gain >= min_gain_pct,
                      spread_c=spread_c, temp_conflict=conflict, temp_note=note)


def corner_camber(corner: str, camber: np.ndarray, load: np.ndarray, ls_expy: float = 1.0,
                  calm: np.ndarray | None = None, spread: np.ndarray | None = None) -> CornerCamber:
    w = tyre_weight(load, ls_expy)
    calm_deg = None if calm is None or not len(calm) else round(float(np.median(calm)), 2)
    if not len(camber) or not w.sum():
        return CornerCamber(corner=corner, mean_deg=None, p10_deg=None, p90_deg=None, calm_deg=calm_deg)
    p10, p90 = np.percentile(camber, [10, 90])
    return CornerCamber(corner=corner, mean_deg=round(float(np.average(camber, weights=w)), 2),
                        p10_deg=round(float(p10), 2), p90_deg=round(float(p90), 2), calm_deg=calm_deg,
                        spread_c=None if spread is None else round(float(np.average(spread, weights=w)), 1))


# --- From a session -----------------------------------------------------------------


def _lap_samples(s: LdSession, lap_number: int, keep) -> dict[str, np.ndarray]:
    """Camber and load per corner, combined G and whether each sample is in the gate (keep, see
    dampers.gate_filter; None = all) during one lap, on the FL camber channel's clock."""
    lap = s.laps[lap_number - 1]
    clock = s.channels["Camber FL"]
    t = clock.time[(clock.time >= lap.start) & (clock.time < lap.end)]
    on = lambda name: np.interp(t, s.channels[name].time, s.channels[name].data)
    out = {"G": np.hypot(on("CG Accel Lateral"), on("CG Accel Longitudinal")), "GLat": np.abs(on("CG Accel Lateral")),
           "Gate": np.ones(len(t), bool) if keep is None else keep(s, lap_number, t)}
    for c in CORNERS:
        out[f"Camber {c}"], out[f"Load {c}"] = on(f"Camber {c}"), on(f"Tire Load {c}")
        if f"Tire Temp Inner {c}" in s.channels and f"Tire Temp Outer {c}" in s.channels:
            out[f"Spread {c}"] = on(f"Tire Temp Inner {c}") - on(f"Tire Temp Outer {c}")
    return out


def _check_channels(s: LdSession) -> None:
    needed = [f"Camber {c}" for c in CORNERS] + [f"Tire Load {c}" for c in CORNERS] + ["CG Accel Lateral", "CG Accel Longitudinal"]
    missing = [n for n in needed if n not in s.channels]
    if missing:
        why = " iRacing doesn't log camber or tyre loads; this works on AC runs." if s.source == "iracing" else ""
        raise CompareError(f"{s.driver}'s session has no {', '.join(missing)}.{why}")


def compute_camber(catalog: Catalog, refs: list[LapRef], st: CamberSettings, gating: MetricSettings | None = None,
                   data_dir: Path | None = None) -> Camber:
    chosen = _selections(catalog, refs, st.scope)
    for _, s, _ in chosen:
        _check_channels(s)
    keep = gate_filter(chosen, st.gate, gating or MetricSettings())
    deltas = offsets(st)
    selections = []
    for ref, s, laps in chosen:
        compound, compounds, curves = tyres_for(s.car, st, data_dir)
        laps_samples = [_lap_samples(s, n, keep) for n in laps]
        x = {k: np.concatenate([l[k] for l in laps_samples]) for k in laps_samples[0]}
        basis = st.g_basis if st.g_basis != "auto" else ("combined" if st.gate == "Braking" else "lateral")
        g = x["GLat"] if basis == "lateral" else x["G"]
        peak = float(np.percentile(g, st.peak_pct)) if len(g) else 0.0
        working = x["Gate"] & (g >= st.min_g * peak)
        if st.max_g is not None:
            working &= g <= st.max_g * peak
        calm = x["G"] < CALM_G
        cam = {c: x[f"Camber {c}"][working] for c in CORNERS}
        load = {c: x[f"Load {c}"][working] for c in CORNERS}
        spread = {c: x[f"Spread {c}"][working] for c in CORNERS if f"Spread {c}" in x}
        by_axle = {c.axle: c for c in curves}
        selections.append(Selection(
            session_id=ref.session_id, lap=ref.lap if st.scope == "lap" else None, laps=laps, driver=s.driver,
            car=s.car, time_s=round(int(working.sum()) / s.channels["Camber FL"].freq, 2), peak_g=round(peak, 3), g_basis=basis,
            compound=compound, compounds=compounds, tyres=curves,
            corners=[corner_camber(c, cam[c], load[c], by_axle["Front" if c[0] == "F" else "Rear"].ls_expy,
                                   x[f"Camber {c}"][calm], spread.get(c)) for c in CORNERS],
            axles=[axle_result(a, [cam[c] for c in cs], [load[c] for c in cs], deltas, by_axle[a], st.min_gain_pct,
                               [spread[c] for c in cs] if all(c in spread for c in cs) else None) for a, cs in AXLES.items()],
        ))
    note = ("Grip vs camber is AC's tyres.ini curve, read as a parabola peaking at DCAMBER_0 / (2·|DCAMBER_1|); each tyre "
            "counts by load^LS_EXPY. The curve is flat near its peak, so a change that gains less than the 'worthwhile' limit "
            "is within what the model can tell apart. Tyre temperatures are only used as a cross-check; wear, camber thrust "
            "and AC's thermal tyre model aren't scored. Confirm with a back-to-back test before trusting the exact number.")
    return Camber(settings=st, curve_deg=CURVE_DEG.tolist(), selections=selections, note=note)
