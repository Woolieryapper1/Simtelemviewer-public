"""Tyre pressure optimiser (AC only): where each tyre's hot pressure and tread temperature ran near the limit,
scored against the tyre's own pressure and temperature curves from tyres.ini.

Pressure and temperature can't be set apart: the air in the tyre expands as the tread heats, so the pressure
the tyre works at (the HOT pressure, what the log shows) is the cold pressure you set plus whatever the
temperature adds. The setup only has the cold number, so the question is: which cold pressure puts the hot
pressure on the tyre's ideal, at the temperature this car, track and stint really give?

From the car's tyres.ini (per axle and compound):
    PRESSURE_IDEAL         the hot pressure with the most grip (psi)
    PRESSURE_D_GAIN        grip lost per psi away from the ideal ("loss of tyre footprint with pressure rise")
    PERFORMANCE_CURVE      a .lut file: grip multiplier against tyre temperature (°C)
    PRESSURE_TEMPERATURE_GAIN  (section ADDITIONAL1) psi gained per °C the tyre heats
    LS_EXPY                a tyre's force grows with load^LS_EXPY (as in camber.py)
The setup's pressure sliders (setup.ini) move in whole steps (1 psi on the MFT02).

ASSUMPTIONS (none of them is documented by Kunos; check with a back-to-back test):
  * the pressure loss is linear and the same above and below the ideal: loss % = 100 · D_GAIN · |hot − ideal|;
  * the temperature curve is read against the mean tread temperature (inner, middle, outer), or the core if
    that's all the log has;
  * PRESSURE_TEMPERATURE_GAIN is psi per °C of tread temperature (the run's own lap-to-lap slope is shown
    next to it as a cross-check);
  * a change of cold pressure moves the hot pressure by the same amount, and doesn't change the temperature
    (in reality more pressure heats the tread a little more: the "what if it runs hotter" columns show
    that side).

What is counted: the moments near the limit, chosen as in camber.py (G at least min_g × the peak G, optionally
only in one Metrics gate or G band), each weighted by the tyre's force, load^LS_EXPY. Only the scores
above min_gain_pct points count as worth a change, and as the sliders only move in whole steps, the
advice is also given rounded to the setup's step, with what that leaves on the table.

Scenarios: "if the tread ran N °C hotter" shifts the hot pressure by gain × N and the temperature grip by
the curve, and gives the cold pressure change that would still land on the ideal. Use it for a hotter
day, a longer stint or a quicker car; the cold pressure to type in is the setup's number plus the change.

Not scored: wear, graining and blistering, rolling resistance heating (PRESSURE_RR_GAIN), the tyre's
flex (PRESSURE_FLEX_GAIN), and AC's thermal model (heat soak, cooling by speed). iRacing has no tyre
loads, camber or ideal pressure in the files, so this is AC only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator

from .cars import ac_data_dir, read_ini
from .catalog import Catalog
from .compare import CompareError, LapRef
from .dampers import _selections, gate_filter
from .ingest.motec_ld import LdSession
from .laps import lap_infos
from .maths import CORNERS
from .metrics import MetricSettings

SHIFTS_C = (-10.0, -5.0, 0.0, 5.0, 10.0)  # "what if the tread runs this many °C hotter" columns
WINDOW_FRACTION = 0.99  # the temperature window is where the curve is at least this share of its peak
DEFAULT_STEP_PSI = 1.0  # setup slider step if the car's setup.ini doesn't say
TREAD_ROWS = ("Inner", "Middle", "Outer")
NEEDED = [f"{p} {c}" for c in CORNERS for p in ("Tire Pressure", "Tire Load")] + ["CG Accel Lateral", "CG Accel Longitudinal", "Ground Speed"]


class PressureSettings(BaseModel):
    """What to count and which tyre numbers to use. Sent as query parameters; all optional."""

    scope: Literal["lap", "run"] = Field("run", description="'lap': each compared lap; 'run': every timed lap of each lap's run")
    gate: Literal["all", "Braking", "LS", "MS", "HS", "Power limited"] = Field("all", description="Only count the parts of the lap in this Metrics gate")
    peak_pct: float = Field(98, ge=50, le=100, description="This percentile of G over the whole laps is the peak")
    min_g: float = Field(0.6, ge=0, le=1, description="Only count samples using at least this share of the peak G")
    max_g: float | None = Field(None, gt=0, le=2, description="...and at most this share (no upper limit if not given)")
    g_basis: Literal["auto", "lateral", "combined"] = Field(
        "auto", description="G the limits are shares of: 'lateral' = cornering only, 'combined' = lateral and braking/traction; "
                            "'auto' is lateral, except in the Braking gate")
    min_gain_pct: float = Field(0.3, ge=0, le=5, description="A change only counts as worthwhile if it recovers at least this many points of grip")
    compound: int | None = Field(None, ge=0, description="tyres.ini compound index; the car's default if not given")
    ideal_psi: float | None = Field(None, gt=0, le=60, description="Use this ideal hot pressure instead of PRESSURE_IDEAL from tyres.ini")
    psi_per_c: float | None = Field(None, gt=0, le=1, description="Use this pressure gain (psi per °C of tread) instead of PRESSURE_TEMPERATURE_GAIN from tyres.ini")
    ls_expy: float | None = Field(None, gt=0.2, le=1.5, description="Load-sensitivity exponent instead of LS_EXPY from tyres.ini")

    @model_validator(mode="after")
    def _band(self):
        if self.max_g is not None and self.max_g <= self.min_g:
            raise ValueError("The upper G limit must be above the lower one")
        return self


class TyreNumbers(BaseModel):
    """One axle's numbers from tyres.ini."""

    axle: str
    ideal_psi: float
    d_gain: float  # grip share lost per psi away from the ideal
    static_psi: float | None  # PRESSURE_STATIC: the file's cold default, for reference
    ls_expy: float
    psi_per_c: float
    step_psi: float
    window_c: tuple[float, float]  # tread temperatures where the curve is within 1% of its peak
    curve_c: list[float]
    curve_pct: list[float]  # temperature grip, % of the curve's peak, at curve_c
    source: str


class Scenario(BaseModel):
    shift_c: float  # tread temperature change from the run's
    hot_psi: float  # hot pressure the run would have
    cold_change_psi: float  # cold pressure change that puts that on the ideal (- = lower the cold pressure)
    temp_grip_pct: float  # temperature grip at the shifted temperature, % of the peak
    loss_if_unchanged_pct: float  # pressure grip lost if the cold pressure isn't changed


class CornerPressure(BaseModel):
    corner: str
    hot_psi: float | None  # weighted mean hot pressure near the limit
    p10_psi: float | None
    p90_psi: float | None
    ideal_psi: float | None = None
    error_psi: float | None = None  # hot − ideal (+ = too much pressure)
    loss_pct: float | None = None  # grip lost to the pressure error, in points
    tread_c: float | None = None
    core_c: float | None = None
    temp_grip_pct: float | None = None  # weighted mean temperature grip, % of the curve's peak
    temp_state: str | None = None  # "cool", "in window", "hot"
    change_psi: float | None = None  # cold pressure change that brings the mean hot pressure onto the ideal
    rounded_change_psi: float | None = None  # ...rounded to the setup's step
    left_pct: float | None = None  # pressure grip still lost after the rounded change
    worthwhile: bool = False
    scenarios: list[Scenario] = []


class LapPoint(BaseModel):
    lap: int
    hot_psi: dict[str, float]
    tread_c: dict[str, float]


class Selection(BaseModel):
    session_id: str
    lap: int | None  # None for a whole run
    laps: list[int]
    driver: str
    car: str
    time_s: float  # time counted (near the limit, in the gate)
    peak_g: float
    g_basis: str
    compound: str
    compounds: list[str]
    tyres: list[TyreNumbers]
    corners: list[CornerPressure]
    by_lap: list[LapPoint]
    measured_psi_per_c: float | None  # the run's own lap-to-lap pressure gain per °C of tread; a cross-check on psi_per_c
    temp_source: str  # which temperature the curve was read against


class Pressure(BaseModel):
    settings: PressureSettings
    shifts_c: list[float]
    selections: list[Selection]
    note: str


# --- The tyre ---------------------------------------------------------------------


def parse_lut(text: str) -> tuple[np.ndarray, np.ndarray]:
    """x and y of an AC .lut file ("x|y" per line; blank lines and ; comments are skipped), sorted by x."""
    xs, ys = [], []
    for line in text.splitlines():
        line = line.split(";")[0].strip()
        if not line or "|" not in line:
            continue
        a, b = line.split("|")[:2]
        xs.append(float(a)), ys.append(float(b))
    if len(xs) < 2:
        raise ValueError("a .lut curve needs at least two points")
    order = np.argsort(xs)
    return np.asarray(xs)[order], np.asarray(ys)[order]


def temp_grip(temp_c, curve: tuple[np.ndarray, np.ndarray]):
    """The temperature curve's value (grip multiplier) at temp_c, held flat past its ends."""
    return np.interp(temp_c, curve[0], curve[1])


def temp_window(curve: tuple[np.ndarray, np.ndarray], fraction: float = WINDOW_FRACTION) -> tuple[float, float]:
    """The tread temperatures where the curve is at least `fraction` of its peak (lowest and highest)."""
    grid = np.arange(curve[0][0], curve[0][-1] + 0.01, 0.5)
    ok = grid[temp_grip(grid, curve) >= fraction * curve[1].max()]
    return (float(ok.min()), float(ok.max())) if len(ok) else (float("nan"), float("nan"))


def pressure_loss_pct(hot_psi, ideal_psi: float, d_gain: float):
    """Grip lost (points) to a hot pressure away from the ideal: linear, the same either side (see module docstring)."""
    return 100.0 * d_gain * np.abs(np.asarray(hot_psi, dtype=float) - ideal_psi)


def tyre_weight(load_n: np.ndarray, ls_expy: float) -> np.ndarray:
    return np.maximum(load_n, 0.0) ** ls_expy


def round_to_step(change_psi: float, step: float) -> float:
    return round(change_psi / step) * step if step > 0 else change_psi


def temp_state(tread_c: float, window: tuple[float, float]) -> str:
    return "cool" if tread_c < window[0] else "hot" if tread_c > window[1] else "in window"


def compounds_in(ini: dict[str, dict[str, str]]) -> list[str]:
    names, i = [], 0
    while (front := ini.get("FRONT" if i == 0 else f"FRONT_{i}")) is not None:
        names.append(front.get("NAME") or front.get("SHORT_NAME") or f"Compound {i}")
        i += 1
    return names


def _float(sec: dict[str, str], key: str) -> float | None:
    raw = sec.get(key)
    if raw is None:
        return None
    try:
        return float(raw.split(";")[0].strip())
    except ValueError:
        return None


def setup_step(data_dir: Path, slider: str) -> float:
    """Pressure slider step (setup.ini section PRESSURE_LF, PRESSURE_LR...), psi."""
    path = data_dir / "setup.ini"
    if not path.is_file():
        return DEFAULT_STEP_PSI
    return _float(read_ini(path.read_text(errors="ignore")).get(slider, {}), "STEP") or DEFAULT_STEP_PSI


def tyres_for(car: str, st: PressureSettings, data_dir: Path | None = None
              ) -> tuple[str, list[str], list[TyreNumbers], dict[str, tuple[np.ndarray, np.ndarray]]]:
    """(compound name, all compound names, front and rear numbers, temperature curve per axle) from the car's tyres.ini."""
    data_dir = data_dir or ac_data_dir(car)
    path = data_dir / "tyres.ini" if data_dir else None
    if path is None or not path.is_file():
        raise CompareError(
            f"Can't find {car}'s tyres.ini. Kunos cars keep it packed in data.acd: unpack it with Content Manager "
            f"and copy the data folder to 'FS Telemetry\\cars\\{car}\\data'.")
    ini = read_ini(path.read_text(errors="ignore"))
    names = compounds_in(ini)
    if not names:
        raise CompareError(f"{path} has no [FRONT] tyre section")
    default = ini.get("COMPOUND_DEFAULT", {}).get("INDEX", "0")
    i = st.compound if st.compound is not None else (int(default) if default.isdigit() else 0)
    if i >= len(names):
        raise CompareError(f"{car} has {len(names)} tyre compound(s); there's no compound {i}")
    suffix = "" if i == 0 else f"_{i}"
    extra = ini.get("ADDITIONAL1", {})
    out, curves = [], {}
    for axle in ("Front", "Rear"):
        sec = ini.get(f"{axle.upper()}{suffix}", {})
        thermal = ini.get(f"THERMAL_{axle.upper()}{suffix}", {})
        ideal, d_gain = st.ideal_psi or _float(sec, "PRESSURE_IDEAL"), _float(sec, "PRESSURE_D_GAIN")
        if ideal is None or d_gain is None:
            raise CompareError(f"{path} has no PRESSURE_IDEAL / PRESSURE_D_GAIN for the {axle.lower()} {names[i]} tyre")
        lut_name = (thermal.get("PERFORMANCE_CURVE") or "").split(";")[0].strip()
        lut_path = path.parent / lut_name
        if not lut_name or not lut_path.is_file():
            raise CompareError(f"Can't find the {axle.lower()} tyre's temperature curve '{lut_name}' next to {path.name}")
        curve = parse_lut(lut_path.read_text(errors="ignore"))
        gain = st.psi_per_c or _float(extra, "PRESSURE_TEMPERATURE_GAIN")
        if gain is None:
            raise CompareError(f"{path} has no PRESSURE_TEMPERATURE_GAIN: type the pressure gain per °C in")
        peak = float(curve[1].max())
        grid = np.arange(0.0, 131.0, 5.0)
        step = setup_step(path.parent, "PRESSURE_LF" if axle == "Front" else "PRESSURE_LR")
        curves[axle] = curve
        out.append(TyreNumbers(
            axle=axle, ideal_psi=ideal, d_gain=d_gain, static_psi=_float(sec, "PRESSURE_STATIC"),
            ls_expy=st.ls_expy or _float(sec, "LS_EXPY") or 1.0, psi_per_c=gain, step_psi=step,
            window_c=temp_window(curve), curve_c=grid.tolist(),
            curve_pct=[round(float(v), 2) for v in 100.0 * temp_grip(grid, curve) / peak],
            source=f"tyres.ini, {names[i]}"))
    return names[i], names, out, curves


# --- The maths, on plain arrays (easy to test) -------------------------------------


def corner_pressure(corner: str, hot: np.ndarray, tread: np.ndarray, core: np.ndarray | None, load: np.ndarray,
                    tyre: TyreNumbers, curve: tuple[np.ndarray, np.ndarray], min_gain_pct: float = 0.3,
                    psi_per_c: float | None = None) -> CornerPressure:
    """One tyre's result from its samples near the limit: hot pressure (psi), tread and core temperature (°C), load (N)."""
    w = tyre_weight(load, tyre.ls_expy)
    if not len(hot) or not w.sum():
        return CornerPressure(corner=corner, hot_psi=None, p10_psi=None, p90_psi=None)
    gain = psi_per_c or tyre.psi_per_c
    peak = float(curve[1].max())
    mean = lambda x: float(np.average(x, weights=w))
    hot_mean, tread_mean = mean(hot), mean(tread)
    error = hot_mean - tyre.ideal_psi
    change = -error
    rounded = round_to_step(change, tyre.step_psi)
    # Grip the pressure costs now, and what is left after the rounded change (the mean moves by the change)
    loss = float(pressure_loss_pct(hot_mean, tyre.ideal_psi, tyre.d_gain))
    left = float(pressure_loss_pct(hot_mean + rounded, tyre.ideal_psi, tyre.d_gain))
    p10, p90 = np.percentile(hot, [10, 90])
    scenarios = []
    for shift in SHIFTS_C:
        hot_at = hot_mean + gain * shift
        scenarios.append(Scenario(
            shift_c=shift, hot_psi=round(hot_at, 2), cold_change_psi=round(tyre.ideal_psi - hot_at, 2),
            temp_grip_pct=round(float(100.0 * np.average(temp_grip(tread + shift, curve), weights=w) / peak), 2),
            loss_if_unchanged_pct=round(float(pressure_loss_pct(hot_at, tyre.ideal_psi, tyre.d_gain)), 2)))
    return CornerPressure(
        corner=corner, hot_psi=round(hot_mean, 2), p10_psi=round(float(p10), 2), p90_psi=round(float(p90), 2),
        ideal_psi=tyre.ideal_psi, error_psi=round(error, 2), loss_pct=round(loss, 2), tread_c=round(tread_mean, 1),
        core_c=None if core is None else round(mean(core), 1),
        temp_grip_pct=round(float(100.0 * np.average(temp_grip(tread, curve), weights=w) / peak), 2),
        temp_state=temp_state(tread_mean, tyre.window_c), change_psi=round(change, 2), rounded_change_psi=round(rounded, 2),
        left_pct=round(left, 2), worthwhile=(loss - left) >= min_gain_pct, scenarios=scenarios)


def measured_gain(laps: list[LapPoint]) -> float | None:
    """psi per °C from lap to lap: the slope of each corner's lap-mean pressure on its lap-mean tread
    temperature, with every corner's own average taken out first (so a tyre set higher or lower than another
    doesn't count), pooled over the four corners. None with fewer than 4 laps or no spread in temperature."""
    if len(laps) < 4:
        return None
    xs, ys = [], []
    for c in CORNERS:
        t = np.array([l.tread_c[c] for l in laps if c in l.tread_c and c in l.hot_psi])
        p = np.array([l.hot_psi[c] for l in laps if c in l.tread_c and c in l.hot_psi])
        if len(t) >= 4:
            xs.append(t - t.mean()), ys.append(p - p.mean())
    if not xs:
        return None
    x, y = np.concatenate(xs), np.concatenate(ys)
    return round(float((x * y).sum() / (x * x).sum()), 3) if (x * x).sum() > 1.0 else None


# --- From a session -----------------------------------------------------------------


def _tread_names(s: LdSession, c: str) -> list[str]:
    return [f"Tire Temp {r} {c}" for r in TREAD_ROWS if f"Tire Temp {r} {c}" in s.channels]


def _check_channels(s: LdSession) -> None:
    missing = [n for n in NEEDED if n not in s.channels]
    for c in CORNERS:
        if not _tread_names(s, c) and f"Tire Temp Core {c}" not in s.channels:
            missing.append(f"Tire Temp {c}")
    if missing:
        why = " iRacing doesn't log tyre loads and has no tyres.ini; this works on AC runs." if s.source == "iracing" else ""
        raise CompareError(f"{s.driver}'s session has no {', '.join(missing)}.{why}")


def _lap_samples(s: LdSession, lap_number: int, keep) -> dict[str, np.ndarray]:
    """Hot pressure, tread and core temperature and load per corner, G, and the gate flag, during one lap,
    on the tyre-load clock (30 Hz; the pressure and temperature channels are slower and are interpolated)."""
    lap = s.laps[lap_number - 1]
    clock = s.channels["Tire Load FL"]
    t = clock.time[(clock.time >= lap.start) & (clock.time < lap.end)]
    on = lambda name: np.interp(t, s.channels[name].time, s.channels[name].data)
    out = {"G": np.hypot(on("CG Accel Lateral"), on("CG Accel Longitudinal")), "GLat": np.abs(on("CG Accel Lateral")),
           "Speed": on("Ground Speed"), "Gate": np.ones(len(t), bool) if keep is None else keep(s, lap_number, t)}
    for c in CORNERS:
        out[f"Hot {c}"], out[f"Load {c}"] = on(f"Tire Pressure {c}"), on(f"Tire Load {c}")
        rows = _tread_names(s, c)
        core = f"Tire Temp Core {c}" in s.channels
        out[f"Tread {c}"] = np.mean([on(n) for n in rows], axis=0) if rows else on(f"Tire Temp Core {c}")
        if core:
            out[f"Core {c}"] = on(f"Tire Temp Core {c}")
    return out


def _by_lap(s: LdSession, laps: list[int]) -> list[LapPoint]:
    """Whole-lap mean hot pressure and tread temperature per corner (running, i.e. not in the pits or stopped)."""
    out = []
    for n in laps:
        x = _lap_samples(s, n, None)
        moving = x["Speed"] > 20
        pick = (lambda a: a[moving]) if moving.any() else (lambda a: a)
        out.append(LapPoint(lap=n, hot_psi={c: round(float(pick(x[f"Hot {c}"]).mean()), 2) for c in CORNERS},
                            tread_c={c: round(float(pick(x[f"Tread {c}"]).mean()), 1) for c in CORNERS}))
    return out


def compute_pressure(catalog: Catalog, refs: list[LapRef], st: PressureSettings, gating: MetricSettings | None = None,
                     data_dir: Path | None = None) -> Pressure:
    chosen = _selections(catalog, refs, st.scope)
    for _, s, _ in chosen:
        _check_channels(s)
    keep = gate_filter(chosen, st.gate, gating or MetricSettings())
    selections = []
    for ref, s, laps in chosen:
        compound, compounds, tyres, curves = tyres_for(s.car, st, data_dir)
        samples = [_lap_samples(s, n, keep) for n in laps]
        x = {k: np.concatenate([l[k] for l in samples]) for k in samples[0]}
        basis = st.g_basis if st.g_basis != "auto" else ("combined" if st.gate == "Braking" else "lateral")
        g = x["GLat"] if basis == "lateral" else x["G"]
        peak = float(np.percentile(g, st.peak_pct)) if len(g) else 0.0
        working = x["Gate"] & (g >= st.min_g * peak)
        if st.max_g is not None:
            working &= g <= st.max_g * peak
        by_axle = {t.axle: t for t in tyres}
        corners = []
        for c in CORNERS:
            axle = "Front" if c[0] == "F" else "Rear"
            corners.append(corner_pressure(
                c, x[f"Hot {c}"][working], x[f"Tread {c}"][working], x[f"Core {c}"][working] if f"Core {c}" in x else None,
                x[f"Load {c}"][working], by_axle[axle], curves[axle], st.min_gain_pct, st.psi_per_c))
        timed = [i.number for i in lap_infos(s) if i.kind == "timed"]
        by_lap = _by_lap(s, laps)
        selections.append(Selection(
            session_id=ref.session_id, lap=ref.lap if st.scope == "lap" else None, laps=laps, driver=s.driver, car=s.car,
            time_s=round(int(working.sum()) / s.channels["Tire Load FL"].freq, 2), peak_g=round(peak, 3), g_basis=basis,
            compound=compound, compounds=compounds, tyres=tyres, corners=corners, by_lap=by_lap,
            measured_psi_per_c=measured_gain(_by_lap(s, timed) if st.scope == "lap" else by_lap),
            temp_source="mean of the inner, middle and outer tread" if all(_tread_names(s, c) for c in CORNERS) else "core"))
    note = ("Pressure grip is tyres.ini's PRESSURE_IDEAL and PRESSURE_D_GAIN, read as a loss that is linear in the "
            "distance from the ideal, the same either side; temperature grip is its temperature curve, read against the "
            "tread temperature. Each moment counts by the tyre's force (load^LS_EXPY). The advice is a change to the "
            "cold pressure, assuming the hot pressure moves one for one with it and the temperature stays. The "
            "'hotter by' columns move the hot pressure by the car's psi per °C. Wear, graining and AC's thermal model "
            "aren't scored. Confirm with a back-to-back run before trusting the exact number.")
    return Pressure(settings=st, shifts_c=list(SHIFTS_C), selections=selections, note=note)
