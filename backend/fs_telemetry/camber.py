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
    score(δ) = Σ Fz · f(γ(t) + δ)  /  Σ Fz · f(γ*)       (100% = always at the peak)
over the samples near the limit: combined G at least min_g (and at most max_g,
if given) × the selection's peak G. The peak is a percentile of combined G over
the selection's whole laps (peak_pct, default the 98th, ignoring one-sample
spikes from kerbs). Weighting by load makes the outside tyre count most, as it
does most of the cornering; the inside tyre and braking pull the answer back.

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
average camber positive aren't tried: no race car wants it.

Not scored: tyre temperatures and wear, camber thrust (CAMBER_GAIN) and the
small change in camber gain with static camber. iRacing logs no camber, so
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


class CamberSettings(BaseModel):
    """What to count and which tyre curve to use. Sent as query parameters; all optional."""

    scope: Literal["lap", "run"] = Field("run", description="'lap': each compared lap; 'run': every timed lap of each lap's run")
    gate: Literal["all", "Braking", "LS", "MS", "HS", "Power limited"] = Field("all", description="Only count the parts of the lap in this Metrics gate")
    peak_pct: float = Field(98, ge=50, le=100, description="This percentile of combined G over the whole laps is the peak")
    min_g: float = Field(0.6, ge=0, le=1, description="Only count samples using at least this share of the peak combined G")
    max_g: float | None = Field(None, gt=0, le=2, description="...and at most this share (no upper limit if not given)")
    span_deg: float = Field(3.0, gt=0, le=6, description="Try static camber changes up to this far either way")
    step_deg: float = Field(0.1, ge=0.05, le=1, description="Step between the changes tried")
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


class CornerCamber(BaseModel):
    corner: str
    mean_deg: float | None  # load-weighted average camber while working
    p10_deg: float | None
    p90_deg: float | None


class AxleResult(BaseModel):
    axle: str
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
    peak_g: float  # the peak combined G the limits are shares of
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


def camber_grip(camber_deg, d0: float, d1: float):
    """Grip multiplier for camber (degrees), on the parabola in the module docstring. Never below 0."""
    g = np.abs(np.radians(camber_deg))
    return np.maximum(1.0 + d0 * g + d1 * g * g, 0.0)


def peak_camber_deg(d0: float, d1: float) -> float:
    """The camber with the most grip, as a negative angle."""
    return -math.degrees(d0 / (2.0 * abs(d1)))


def tyre_curve(axle: str, d0: float, d1: float, source: str) -> TyreCurve:
    if d1 >= 0:
        raise CompareError(f"The {axle.lower()} tyre's DCAMBER_1 is {d1}: it has to be negative for the curve to have a peak")
    peak = peak_camber_deg(d0, d1)
    top = float(camber_grip(peak, d0, d1))
    return TyreCurve(axle=axle, dcamber0=d0, dcamber1=d1, peak_deg=round(peak, 2),
                     peak_gain_pct=round(100.0 * (top - 1.0), 2), source=source,
                     grip_pct=[round(float(v), 3) for v in 100.0 * camber_grip(CURVE_DEG, d0, d1) / top])


def compounds_in(ini: dict[str, dict[str, str]]) -> list[str]:
    names, i = [], 0
    while (front := ini.get("FRONT" if i == 0 else f"FRONT_{i}")) is not None:
        names.append(front.get("NAME") or front.get("SHORT_NAME") or f"Compound {i}")
        i += 1
    return names


def tyres_for(car: str, st: CamberSettings, data_dir: Path | None = None) -> tuple[str, list[str], list[TyreCurve]]:
    """(compound name, all compound names, front and rear curves) for a car: typed-in numbers first, then tyres.ini."""
    typed = {a: (getattr(st, f"{a.lower()}_dcamber0"), getattr(st, f"{a.lower()}_dcamber1")) for a in AXLES}
    if all(d0 is not None for d0, _ in typed.values()):
        return "typed in", [], [tyre_curve(a, d0, d1, "typed in") for a, (d0, d1) in typed.items()]

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
        if d0 is not None:
            curves.append(tyre_curve(axle, d0, d1, "typed in"))
            continue
        try:
            d0, d1 = float(sec["DCAMBER_0"]), float(sec["DCAMBER_1"])
        except (KeyError, ValueError):
            raise CompareError(f"{path} has no DCAMBER_0 / DCAMBER_1 for the {axle.lower()} {names[i]} tyre") from None
        curves.append(tyre_curve(axle, d0, d1, f"tyres.ini, {names[i]}"))
    return names[i], names, curves


# --- The maths, on plain arrays (easy to test) -------------------------------------


def offsets(st: CamberSettings) -> np.ndarray:
    n = int(round(st.span_deg / st.step_deg))
    return np.round(np.arange(-n, n + 1) * st.step_deg, 3)


def axle_score(camber_deg: np.ndarray, load_n: np.ndarray, deltas: np.ndarray, curve: TyreCurve) -> np.ndarray:
    """Score (%) for each static camber change in deltas: load-weighted grip over load-weighted peak grip.
    NaN for changes that would make the load-weighted average camber positive (see the module docstring)."""
    w = np.maximum(load_n, 0.0)
    if not w.sum():
        return np.full(len(deltas), np.nan)
    top = float(camber_grip(curve.peak_deg, curve.dcamber0, curve.dcamber1))
    grip = camber_grip(camber_deg[None, :] + deltas[:, None], curve.dcamber0, curve.dcamber1)
    score = 100.0 * (grip * w).sum(axis=1) / (w.sum() * top)
    return np.where(np.average(camber_deg, weights=w) + deltas > 0, np.nan, score)


def axle_result(axle: str, camber: list[np.ndarray], load: list[np.ndarray], deltas: np.ndarray, curve: TyreCurve) -> AxleResult:
    score = axle_score(np.concatenate(camber), np.concatenate(load), deltas, curve)
    if np.isnan(score).all():
        return AxleResult(axle=axle, offsets_deg=deltas.tolist(), score_pct=[], current_pct=None,
                          best_offset_deg=None, best_pct=None, at_limit=False)
    best = int(np.nanargmax(score))
    tried = np.flatnonzero(~np.isnan(score))
    return AxleResult(axle=axle, offsets_deg=deltas.tolist(),
                      score_pct=[None if np.isnan(v) else round(float(v), 3) for v in score],
                      current_pct=None if np.isnan(now := score[len(deltas) // 2]) else round(float(now), 3), best_offset_deg=float(deltas[best]),
                      best_pct=round(float(score[best]), 3), at_limit=best in (tried[0], tried[-1]))


def corner_camber(corner: str, camber: np.ndarray, load: np.ndarray) -> CornerCamber:
    w = np.maximum(load, 0.0)
    if not len(camber) or not w.sum():
        return CornerCamber(corner=corner, mean_deg=None, p10_deg=None, p90_deg=None)
    p10, p90 = np.percentile(camber, [10, 90])
    return CornerCamber(corner=corner, mean_deg=round(float(np.average(camber, weights=w)), 2),
                        p10_deg=round(float(p10), 2), p90_deg=round(float(p90), 2))


# --- From a session -----------------------------------------------------------------


def _lap_samples(s: LdSession, lap_number: int, keep) -> dict[str, np.ndarray]:
    """Camber and load per corner, combined G and whether each sample is in the gate (keep, see
    dampers.gate_filter; None = all) during one lap, on the FL camber channel's clock."""
    lap = s.laps[lap_number - 1]
    clock = s.channels["Camber FL"]
    t = clock.time[(clock.time >= lap.start) & (clock.time < lap.end)]
    on = lambda name: np.interp(t, s.channels[name].time, s.channels[name].data)
    out = {"G": np.hypot(on("CG Accel Lateral"), on("CG Accel Longitudinal")),
           "Gate": np.ones(len(t), bool) if keep is None else keep(s, lap_number, t)}
    for c in CORNERS:
        out[f"Camber {c}"], out[f"Load {c}"] = on(f"Camber {c}"), on(f"Tire Load {c}")
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
        peak = float(np.percentile(x["G"], st.peak_pct)) if len(x["G"]) else 0.0
        working = x["Gate"] & (x["G"] >= st.min_g * peak)
        if st.max_g is not None:
            working &= x["G"] <= st.max_g * peak
        cam = {c: x[f"Camber {c}"][working] for c in CORNERS}
        load = {c: x[f"Load {c}"][working] for c in CORNERS}
        by_axle = {c.axle: c for c in curves}
        selections.append(Selection(
            session_id=ref.session_id, lap=ref.lap if st.scope == "lap" else None, laps=laps, driver=s.driver,
            car=s.car, time_s=round(int(working.sum()) / s.channels["Camber FL"].freq, 2), peak_g=round(peak, 3),
            compound=compound, compounds=compounds, tyres=curves,
            corners=[corner_camber(c, cam[c], load[c]) for c in CORNERS],
            axles=[axle_result(a, [cam[c] for c in cs], [load[c] for c in cs], deltas, by_axle[a]) for a, cs in AXLES.items()],
        ))
    note = ("Grip vs camber is AC's tyres.ini curve, read as a parabola peaking at DCAMBER_0 / (2·|DCAMBER_1|): "
            "confirm with a back-to-back test before trusting the exact number. Tyre temperatures, wear and camber "
            "thrust aren't scored.")
    return Camber(settings=st, curve_deg=CURVE_DEG.tolist(), selections=selections, note=note)
