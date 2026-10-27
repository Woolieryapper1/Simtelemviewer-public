"""Damper optimiser (AC): damping ratios from the car's own files, and settings that hit target ratios.

AC defines springs, anti-roll bars and dampers AT THE WHEEL (wheel rates, N/m and
N·s/m), so no motion ratios are needed. Per axle, from the car's data folder:
    car.ini         [BASIC] TOTALMASS
    suspensions.ini [BASIC] CG_LOCATION (front share of the weight); [FRONT]/[REAR]
                    SPRING_RATE, HUB_MASS (unsprung, per wheel), DAMP_BUMP, DAMP_REBOUND,
                    DAMP_FAST_BUMP, DAMP_FAST_REBOUND and the FAST_*THRESHOLD speeds;
                    [ARB] FRONT / REAR
    tyres.ini       RATE (the tyre's vertical stiffness), for the setup's compound
    setup.ini       how the setup's numbers map to real ones (see setup_value)
A setup from the library replaces the suspensions.ini defaults it has values for, and a wheel
rate typed in replaces the spring rate (for cars whose spring the files don't give per corner,
like a heave-spring front, until heave elements are modelled).

Physics, per corner (each corner's sprung mass on its own spring - the usual quarter car):
    sprung mass   m = TOTALMASS × axle weight share / 2 - HUB_MASS
    ride rate     k = spring and tyre in series:  k_s·k_t / (k_s + k_t)
    roll rate     the same with k_s + 2·k_arb (in roll the bar pushes each wheel with
                  twice its rate, as the two wheels move opposite ways)
    critical damping  c_crit = 2·√(k·m);   damping ratio ζ = c / c_crit
    frequency     √(k/m) / 2π
    wheel hop     the unsprung mass between spring and tyre: √((k_s + k_t) / m_u) / 2π
Targets are race-car rules of thumb and are settings: low speed (body control)
bump ζ 0.6, rebound 0.9; high speed (bumps, kerbs) bump 0.3, rebound 0.45. They're
applied to the ride (heave) ratio; the roll ratio is shown alongside.

From the telemetry, to check a change back to back (needs AC tyre loads):
    load variation   how much each tyre's load wobbles about its 1-second average,
                     RMS as % of the average load, at speed. Less = steadier grip.
    fast %           share of time each damper is past its fast threshold (bump or
                     rebound). AC logs travel at 30 Hz, so this reads low on kerbs.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from .cars import ac_data_dir, read_ini
from .catalog import Catalog
from .compare import CompareError, LapRef
from .dampers import _selections, damper_channels
from .ingest.motec_ld import LdSession
from .maths import CORNERS
from .setups import SetupLibrary

AXLES = {"Front": ("FL", "FR"), "Rear": ("RL", "RR")}
SETUP_SIDES = {"Front": ("LF", "HF", "RF"), "Rear": ("LR", "HR", "RR")}  # setup field suffixes, first found wins
# (label, suspensions.ini key, setup field, target setting)
DAMPERS = [("Bump", "DAMP_BUMP", "DAMP_BUMP", "ls_bump"), ("Rebound", "DAMP_REBOUND", "DAMP_REBOUND", "ls_rebound"),
           ("Fast bump", "DAMP_FAST_BUMP", "DAMP_FAST_BUMP", "hs_bump"),
           ("Fast rebound", "DAMP_FAST_REBOUND", "DAMP_FAST_REBOUND", "hs_rebound")]
MIN_SPEED_KPH = 60.0  # load variation only counts running at speed (not hairpins and the pit lane)
SMOOTH_S = 1.0  # load variation is about this moving average


class DamperSetupSettings(BaseModel):
    """Which setup and targets. Sent as query parameters; all optional."""

    scope: Literal["lap", "run"] = Field("run", description="'lap': each compared lap; 'run': every timed lap of each lap's run")
    setup_id: str | None = Field(None, description="A setup from the library; the car's defaults if not given")
    ls_bump: float = Field(0.6, gt=0, le=2, description="Target low-speed bump damping ratio")
    ls_rebound: float = Field(0.9, gt=0, le=2, description="Target low-speed rebound damping ratio")
    hs_bump: float = Field(0.3, gt=0, le=2, description="Target high-speed bump damping ratio")
    hs_rebound: float = Field(0.45, gt=0, le=2, description="Target high-speed rebound damping ratio")
    spring_front: float | None = Field(None, gt=0, le=1000, description="Front wheel rate typed in, N/mm; beats the setup")
    spring_rear: float | None = Field(None, gt=0, le=1000, description="Rear wheel rate typed in, N/mm; beats the setup")


class DamperRow(BaseModel):
    name: str  # Bump / Rebound / Fast bump / Fast rebound
    ns_m: float  # damping at the wheel now, N·s/m
    zeta_ride: float
    zeta_roll: float
    target_zeta: float
    target_ns_m: float
    setup_field: str | None  # the setup field that sets it, None if the setup can't change it
    current: str  # in the setup's own units, e.g. "18 clicks" or "4800 N·s/m"
    suggested: str | None
    change: float | None  # suggested minus current, in the setup's units (clicks or N·s/m)


class AxleSetup(BaseModel):
    axle: str
    sprung_kg: float  # per corner
    unsprung_kg: float
    spring_n_mm: float
    spring_from: str  # "typed", "setup" or "car files"
    arb_n_mm: float
    tyre_n_mm: float
    ride_hz: float
    roll_hz: float
    hop_hz: float
    critical_ride_ns_m: float
    fast_bump_m_s: float | None  # speed where the fast bump damping takes over
    fast_rebound_m_s: float | None
    dampers: list[DamperRow]


class CornerCheck(BaseModel):
    corner: str
    load_variation_pct: float | None
    fast_bump_pct: float | None
    fast_rebound_pct: float | None


class Selection(BaseModel):
    session_id: str
    lap: int | None
    laps: list[int]
    driver: str
    corners: list[CornerCheck]


class DamperSetup(BaseModel):
    settings: DamperSetupSettings
    car: str
    setup: str  # which setup the numbers are for, in words
    axles: list[AxleSetup]  # empty if the car's files couldn't be read (see missing)
    missing: str  # why there are no axles, "" if there are
    selections: list[Selection]
    note: str


# --- Car files --------------------------------------------------------------------


def _num(sec: dict[str, str], key: str) -> float | None:
    try:
        return float(sec[key])
    except (KeyError, ValueError):
        return None


def setup_value(field: str, raw: float, setup_ini: dict[str, dict[str, str]]) -> float:
    """A setup file's number -> the real number, by the field's SHOW_CLICKS in setup.ini:
        0  the value itself
        1  clicks of STEP from 0 (checked: 100 clicks of 100 = the car's DAMP_BUMP=10000)
        2  steps of STEP from MIN (checked: the Kunos GT-R GT3's ARB 4 and 2 = its default bars,
           32000 + 4 × 9000 and 7650 + 2 × 2200)"""
    sec = setup_ini.get(field, {})
    step = _num(sec, "STEP") or 1.0
    shown = sec.get("SHOW_CLICKS", "0").strip()
    if shown == "1":
        return raw * step
    if shown == "2":
        return (_num(sec, "MIN") or 0.0) + raw * step
    return raw


def setup_n_m(field: str, v: float, setup_ini: dict[str, dict[str, str]]) -> float:
    """A spring or bar rate from a setup -> N/m. Setups give springs in N/mm and bars in N/m: a field
    whose setup.ini range tops out under 1000 is in N/mm. With no setup.ini, a number under 1000 is
    taken as N/mm (no real wheel rate is under 1000 N/m). The car files always hold N/m."""
    hi = _num(setup_ini.get(field, {}), "MAX")
    return v * 1000.0 if (hi < 1000 if hi is not None else v < 1000) else v


def from_setup(field: str, real: float, setup_ini: dict[str, dict[str, str]]) -> tuple[str, float]:
    """A real damper rate -> (as the setup shows it, the number the setup stores), on the setup's
    steps and inside its range."""
    sec = setup_ini.get(field, {})
    step = _num(sec, "STEP") or 1.0
    lo, hi = _num(sec, "MIN"), _num(sec, "MAX")
    real = min(max(real, lo if lo is not None else real), hi if hi is not None else real)
    shown = sec.get("SHOW_CLICKS", "0").strip()
    if shown in ("1", "2"):  # clicks from 0, or steps from MIN (see setup_value)
        start = (lo or 0.0) if shown == "2" else 0.0
        clicks = round((real - start) / step)
        return f"{clicks} clicks", float(clicks)
    v = round(real / step) * step
    return f"{v:.0f} N·s/m", v


def series(a: float, b: float) -> float:
    return a * b / (a + b)


def axle_setup(axle: str, sus: dict, total_kg: float, front_share: float, tyre_n_m: float, arb_n_m: float,
               values: dict, setup_ini: dict, st: DamperSetupSettings, heave: dict | None = None) -> AxleSetup:
    """The physics for one axle. sus: its suspensions.ini section; values: the setup's fields (may be empty);
    heave: its [HEAVE_FRONT]/[HEAVE_REAR] section if the car has one (only named in the error, not modelled)."""
    side = next((s for s in SETUP_SIDES[axle] if any(f"{k}_{s}" in values for _, _, k, _ in DAMPERS)
                 or f"SPRING_RATE_{s}" in values), SETUP_SIDES[axle][0])
    from_values = lambda field: setup_value(field, float(values[field]), setup_ini) if isinstance(values.get(field), (int, float)) else None

    unsprung = _num(sus, "HUB_MASS") or 0.0
    sprung = total_kg * (front_share if axle == "Front" else 1 - front_share) / 2 - unsprung
    if sprung <= 0:
        raise CompareError(f"The {axle.lower()} sprung mass works out at {sprung:.0f} kg: check TOTALMASS, CG_LOCATION and HUB_MASS")
    # A typed wheel rate beats the setup, which beats the car's defaults
    typed, from_setup_v = st.spring_front if axle == "Front" else st.spring_rear, from_values(f"SPRING_RATE_{side}")
    if typed:
        spring, spring_from = typed * 1000.0, "typed"
    elif from_setup_v:
        spring, spring_from = setup_n_m(f"SPRING_RATE_{side}", from_setup_v, setup_ini), "setup"
    else:
        spring, spring_from = _num(sus, "SPRING_RATE"), "car files"
    if not spring:
        msg = (f"No {axle.lower()} spring rate: the {axle.upper()} SPRING_RATE in suspensions.ini is 0 or missing. "
               f"Pick a setup for this car or type the {axle.lower()} wheel rate.")
        if heave_rate := _num(heave or {}, "SPRING_RATE"):
            msg += (f" This car has a {axle.lower()} heave spring ([HEAVE_{axle.upper()}] SPRING_RATE "
                    f"{heave_rate / 1000:.0f} N/mm), which the optimiser doesn't model yet.")
        raise CompareError(msg)
    ride_k, roll_k = series(spring, tyre_n_m), series(spring + 2 * arb_n_m, tyre_n_m)
    crit_ride, crit_roll = 2 * math.sqrt(ride_k * sprung), 2 * math.sqrt(roll_k * sprung)

    rows = []
    for name, sus_key, field_base, target_key in DAMPERS:
        field = f"{field_base}_{side}"
        now = from_values(field)
        if now is None:
            now = _num(sus, sus_key)
        if not now:  # missing, or 0: no damper at this corner (e.g. a heave car's front corners)
            continue
        target = getattr(st, target_key)
        settable = field in setup_ini or field in values
        current, cur_v = from_setup(field, now, setup_ini) if settable else (f"{now:.0f} N·s/m", now)
        suggested, sug_v = from_setup(field, target * crit_ride, setup_ini) if settable else (None, None)
        rows.append(DamperRow(
            name=name, ns_m=round(now), zeta_ride=round(now / crit_ride, 3), zeta_roll=round(now / crit_roll, 3),
            target_zeta=target, target_ns_m=round(target * crit_ride), setup_field=field if settable else None,
            current=current, suggested=suggested, change=None if sug_v is None else round(sug_v - cur_v, 3)))

    fast = lambda k: (_num(sus, k) or None)
    return AxleSetup(
        axle=axle, sprung_kg=round(sprung, 1), unsprung_kg=unsprung, spring_n_mm=round(spring / 1000, 2),
        spring_from=spring_from, arb_n_mm=round(arb_n_m / 1000, 2), tyre_n_mm=round(tyre_n_m / 1000, 1),
        ride_hz=round(math.sqrt(ride_k / sprung) / (2 * math.pi), 2),
        roll_hz=round(math.sqrt(roll_k / sprung) / (2 * math.pi), 2),
        hop_hz=round(math.sqrt((spring + tyre_n_m) / unsprung) / (2 * math.pi), 1) if unsprung else 0.0,
        critical_ride_ns_m=round(crit_ride), fast_bump_m_s=fast("DAMP_FAST_BUMPTHRESHOLD"),
        fast_rebound_m_s=fast("DAMP_FAST_REBOUNDTHRESHOLD"), dampers=rows)


def car_setup(data: Path, values: dict, st: DamperSetupSettings) -> list[AxleSetup]:
    """Both axles from a car's data folder and a setup's values ({} = the car's defaults)."""
    read = lambda name: read_ini((data / name).read_text(errors="ignore")) if (data / name).is_file() else {}
    car, sus, tyres, setup_ini = read("car.ini"), read("suspensions.ini"), read("tyres.ini"), read("setup.ini")
    total = _num(car.get("BASIC", {}), "TOTALMASS")
    share = _num(sus.get("BASIC", {}), "CG_LOCATION")
    if not total or share is None:
        raise CompareError(f"{data} is missing TOTALMASS (car.ini) or CG_LOCATION (suspensions.ini)")
    compound = int(values["TYRES"]) if isinstance(values.get("TYRES"), (int, float)) else \
        int(_num(tyres.get("COMPOUND_DEFAULT", {}), "INDEX") or 0)
    out = []
    for axle in AXLES:
        sec = sus.get(axle.upper())
        if not sec:
            raise CompareError(f"{data / 'suspensions.ini'} has no [{axle.upper()}] section")
        tyre = _num(tyres.get(axle.upper() if compound == 0 else f"{axle.upper()}_{compound}", {}), "RATE") \
            or _num(tyres.get(axle.upper(), {}), "RATE")
        if not tyre:
            raise CompareError(f"No {axle.lower()} tyre RATE in tyres.ini")
        arb_field = f"ARB_{axle.upper()}"
        arb = setup_n_m(arb_field, setup_value(arb_field, float(values[arb_field]), setup_ini), setup_ini) \
            if isinstance(values.get(arb_field), (int, float)) else _num(sus.get("ARB", {}), axle.upper()) or 0.0
        out.append(axle_setup(axle, sec, total, share, tyre, arb, values, setup_ini, st,
                              sus.get(f"HEAVE_{axle.upper()}")))
    return out


# --- Telemetry check --------------------------------------------------------------------


def load_variation_pct(load: np.ndarray, hz: float) -> float | None:
    """RMS of the load about its SMOOTH_S moving average, as % of the average load."""
    n = max(int(round(SMOOTH_S * hz)), 1)
    if len(load) < 2 * n or load.mean() <= 0:
        return None
    smooth = np.convolve(load, np.ones(n) / n, mode="same")
    core = slice(n, len(load) - n)  # the moving average is short of samples at the ends
    return round(100.0 * float(np.sqrt(np.mean((load[core] - smooth[core]) ** 2))) / float(load[core].mean()), 2)


def corner_checks(s: LdSession, laps: list[int], fast: dict[str, tuple[float | None, float | None]]) -> list[CornerCheck]:
    try:
        names = damper_channels(s)
    except CompareError:
        names = {}
    out = []
    for c in CORNERS:
        lv = fb = fr = None
        load = s.channels.get(f"Tire Load {c}")
        if load is not None and "Ground Speed" in s.channels:
            # Each unbroken stretch at speed on its own, so slow corners don't bleed into the average
            vals, weights = [], []
            for n in laps:
                lap = s.laps[n - 1]
                m = (load.time >= lap.start) & (load.time < lap.end)
                at_speed = np.interp(load.time[m], s.channels["Ground Speed"].time, s.channels["Ground Speed"].data) >= MIN_SPEED_KPH
                edges = np.flatnonzero(np.diff(np.r_[0, at_speed.astype(int), 0]))
                for a, b in zip(edges[::2], edges[1::2]):
                    if (v := load_variation_pct(load.data[m][a:b], load.freq)) is not None:
                        vals.append(v)
                        weights.append(b - a)
            lv = round(float(np.average(vals, weights=weights)), 2) if vals else None
        bump_th, reb_th = fast.get(c, (None, None))
        if c in names and (bump_th or reb_th):
            ch = s.channels[names[c]]
            v = np.concatenate([ch.data[(ch.time >= s.laps[n - 1].start) & (ch.time < s.laps[n - 1].end)] for n in laps])
            if len(v):
                fb = round(100.0 * float(np.mean(v >= bump_th * 1000)), 2) if bump_th else None
                fr = round(100.0 * float(np.mean(v <= -reb_th * 1000)), 2) if reb_th else None
        out.append(CornerCheck(corner=c, load_variation_pct=lv, fast_bump_pct=fb, fast_rebound_pct=fr))
    return out


def compute_damper_setup(catalog: Catalog, refs: list[LapRef], st: DamperSetupSettings,
                         library: SetupLibrary | None = None, data_dir: Path | None = None) -> DamperSetup:
    chosen = _selections(catalog, refs, st.scope)
    car = chosen[0][1].car
    values, setup_name = {}, "the car's defaults (suspensions.ini)"
    if st.setup_id:
        setup = (library or SetupLibrary()).get(st.setup_id)
        if setup is None:
            raise CompareError(f"No setup {st.setup_id} in the library")
        if setup.car != car:
            raise CompareError(f"Setup '{setup.name}' is for {setup.car}, but these laps are in {car}")
        values, setup_name = setup.values, f"setup '{setup.name}'"

    axles, missing = [], ""
    data = data_dir or ac_data_dir(car)
    if chosen[0][1].source != "ac":
        missing = "The damper maths needs AC's car files; this is an iRacing run."
    elif data is None:
        missing = (f"Can't find {car}'s car files. Kunos cars keep them packed in data.acd: unpack it with Content "
                   f"Manager and copy the data folder to 'FS Telemetry\\cars\\{car}\\data'.")
    else:
        axles = car_setup(data, values, st)
    fast = {c: (a.fast_bump_m_s, a.fast_rebound_m_s) for a in axles for c in AXLES[a.axle]}

    selections = [Selection(session_id=ref.session_id, lap=ref.lap if st.scope == "lap" else None, laps=laps,
                            driver=s.driver, corners=corner_checks(s, laps, fast)) for ref, s, laps in chosen]
    note = ("Quarter-car damping ratios at the wheel, from the car files and the chosen setup. Targets are rules of "
            "thumb: use them as a starting point, then judge a change by load variation on a back-to-back run.")
    return DamperSetup(settings=st, car=car, setup=setup_name, axles=axles, missing=missing,
                       selections=selections, note=note)
