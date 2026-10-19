"""Per-lap numbers for a whole run ("stint"): fuel, tyre wear, temps, driver inputs.

A telemetrick export is one run out of the pits and back (3-10 laps), so a stint
here is one session file. Each lap gets one row of numbers; the summary averages
the representative laps (full timed laps that weren't invalidated).

How each number is worked out:
  * "used this lap" (fuel, vkm, grip, energy): value at the lap's end minus at its start
  * averages (temps, pressures): time-average over the lap
  * driver % (throttle, braking...): share of the lap's *distance*, so a slow corner
    doesn't count for more just because the car spends longer there

Which laps count is up to the LapFilter (see classify_laps): out and unfinished
laps are left out by default, and so are laps far slower than the best one
(a cool-down or a spin), so a single odd lap can't skew the averages. Laps the
team has marked "use" or "ignore" (session notes, see notes.py) follow the mark.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from .ingest.motec_ld import Channel, Lap, LdSession
from .laps import LapInfo, lap_infos, resample_lap
from .metrics import MetricSettings
from .sectors import split_times

CORNERS = ("FL", "FR", "RL", "RR")
# Some lap-reset channels (e.g. KERS energy) still show last lap's value for a
# sample or so after the line; skip this much at the start of each lap.
RESET_GRACE_S = 1.5



class LapFilter(BaseModel):
    """Which laps the stint averages are taken over. Sent as query parameters; all optional."""

    include_partial: bool = Field(False, description="Count out and unfinished laps in the averages and charts "
                                  "(they never count towards lap times: those aren't full laps)")
    slow_filter: Literal["percent", "first_last", "off"] = Field(
        "percent", description="'percent': leave out laps slower than the best valid lap by more than slow_pct; "
        "'first_last': leave out the first and last timed lap; 'off': keep every timed lap")
    slow_pct: float = Field(10, gt=0, description="With slow_filter=percent: how much slower than the best valid lap (in %) "
                            "a lap can be before it's left out")


class LapMetrics(BaseModel):
    number: int
    kind: str
    invalid: bool
    excluded: str | None = None  # why the lap is left out of the averages ("out lap", "slow +14%"...); None if it counts
    flag: str | None = None  # "use" / "ignore" if the team marked the lap (session notes), else None
    in_pace: bool = False  # counts towards lap-time numbers: a full, valid lap that wasn't left out
    in_averages: bool = False  # counts towards fuel / tyre / input averages (invalid laps still burn fuel)
    lap_time_s: float
    splits_s: list[float]  # official sector times; empty for out/unfinished laps
    top_speed_kph: float
    g_abs_avg: float | None  # G absolute = sqrt(lat² + long²), lap average
    g_abs_p95: float | None  # ...95th percentile: how much grip the lap used near its peak
    fuel_used: float | None  # litres
    fuel_left: float | None
    vkm: dict[str, float]  # virtual km added this lap, per tyre
    grip_end: dict[str, float]  # tyre rubber grip % at the end of the lap
    grip_lost: dict[str, float]  # grip % lost during the lap
    core_temp_avg: dict[str, float]  # °C
    pressure_avg: dict[str, float]  # psi
    kers_deployed_kj: float | None
    kers_charge_end: float | None  # %
    full_throttle_pct: float
    braking_pct: float
    coasting_pct: float
    overlap_pct: float  # throttle and brake at the same time


class StintSummary(BaseModel):
    representative_laps: int  # full, valid timed laps used for the lap-time numbers
    averaged_laps: int = 0  # laps used for the fuel / tyre / input averages
    best_lap_s: float | None
    best_splits_s: list[float]  # quickest of each sector over the full valid laps
    theoretical_best_s: float | None  # sum of best_splits_s
    average_lap_s: float | None
    consistency_s: float | None  # standard deviation of representative lap times
    fuel_per_lap: float | None
    fuel_left: float | None
    laps_of_fuel_left: float | None
    grip_lost_per_lap: dict[str, float]
    vkm_per_lap: dict[str, float]
    kers_per_lap_kj: float | None
    g_abs_avg: float | None  # over the full valid laps
    g_abs_p95: float | None


class Stint(BaseModel):
    session_id: str
    driver: str
    car: str
    track: str
    laps: list[LapMetrics]
    summary: StintSummary


def _at(ch: Channel, t: float) -> float:
    return float(np.interp(t, ch.time, ch.data))


def _window(ch: Channel, lap: Lap, skip_start: float = 0.0) -> np.ndarray:
    m = (ch.time >= lap.start + skip_start) & (ch.time < lap.end)
    return ch.data[m]


def _per_corner(s: LdSession, prefix: str, fn) -> dict[str, float]:
    out = {}
    for c in CORNERS:
        ch = s.channels.get(f"{prefix} {c}")
        if ch is not None:
            out[c] = round(float(fn(ch)), 3)
    return out


def _driver_inputs(s: LdSession, lap_number: int, st: MetricSettings) -> dict[str, float]:
    """Pedal shares and G absolute for one lap, measured over distance."""
    wanted = [c for c in ("Throttle Pos", "Brake Pos", "CG Accel Lateral", "CG Accel Longitudinal") if c in s.channels]
    r = resample_lap(s, lap_number, wanted, step_m=2.0)
    thr, brk = r.get("Throttle Pos", np.array([])), r.get("Brake Pos", np.array([]))
    lat, lon = r.get("CG Accel Lateral"), r.get("CG Accel Longitudinal")
    g = np.hypot(lat, lon) if lat is not None and lon is not None and len(lat) else np.array([])
    g_abs = dict(g_abs_avg=round(float(g.mean()), 3) if len(g) else None,
                 g_abs_p95=round(float(np.percentile(g, 95)), 3) if len(g) else None)
    if not len(thr):
        return dict(full_throttle_pct=0.0, braking_pct=0.0, coasting_pct=0.0, overlap_pct=0.0, **g_abs)
    pct = lambda mask: round(100.0 * float(np.mean(mask)), 1)
    return dict(**g_abs,
        full_throttle_pct=pct(thr >= st.full_throttle_pct),
        braking_pct=pct(brk > st.pedal_on_pct),
        coasting_pct=pct((thr < st.pedal_on_pct) & (brk <= st.pedal_on_pct)),
        overlap_pct=pct((thr > st.pedal_on_pct) & (brk > st.pedal_on_pct)),
    )


def lap_metrics(s: LdSession, info: LapInfo, st: MetricSettings, sector_lines: list[float]) -> LapMetrics:
    lap = s.laps[info.number - 1]
    ch = s.channels
    fuel = ch.get("Fuel Level")
    kers_e, kers_c = ch.get("KERS Deployed Energy"), ch.get("KERS Charge")
    grip_start = _per_corner(s, "Tire Rubber Grip", lambda c: _at(c, lap.start))
    grip_end = _per_corner(s, "Tire Rubber Grip", lambda c: _at(c, lap.end))
    kers_window = _window(kers_e, lap, RESET_GRACE_S) if kers_e is not None else np.array([])
    return LapMetrics(
        number=info.number, kind=info.kind, invalid=info.invalid, lap_time_s=round(info.lap_time, 3),
        splits_s=split_times(resample_lap(s, info.number, [], step_m=2.0), sector_lines, info.lap_time) if info.kind == "timed" else [],
        top_speed_kph=round(float(_window(ch["Ground Speed"], lap).max(initial=0)), 1),
        fuel_used=round(_at(fuel, lap.start) - _at(fuel, lap.end), 3) if fuel else None,
        fuel_left=round(_at(fuel, lap.end), 3) if fuel else None,
        vkm=_per_corner(s, "VirtualKm", lambda c: _at(c, lap.end) - _at(c, lap.start)),
        grip_end=grip_end,
        grip_lost={k: round(grip_start[k] - grip_end[k], 3) for k in grip_end},
        core_temp_avg=_per_corner(s, "Tire Temp Core", lambda c: _window(c, lap).mean()),
        pressure_avg=_per_corner(s, "Tire Pressure", lambda c: _window(c, lap).mean()),
        kers_deployed_kj=round(float(kers_window.max()), 1) if len(kers_window) else None,
        kers_charge_end=round(_at(kers_c, lap.end), 1) if kers_c else None,
        **_driver_inputs(s, info.number, st),
    )


def classify_laps(laps: list[LapMetrics], f: LapFilter, flags: dict[int, str] | None = None) -> list[LapMetrics]:
    """Say which laps count. Returns copies with excluded / in_pace / in_averages filled in.

    * Out and unfinished laps aren't full laps: they're left out unless f.include_partial,
      and even then only of the fuel / tyre / input averages, never of lap-time numbers.
    * 'percent': a timed lap more than slow_pct slower than the best valid timed lap is left out
      (the best lap is the yardstick, so a slow in-lap can't move it).
    * 'first_last': the first and last timed lap are left out (only when there are at least
      three, so a short run isn't emptied).
    * Invalid laps stay in the fuel / tyre averages (they burn fuel and wear tyres like any
      other lap) but not in the lap-time numbers.
    * flags ({lap number: "use" / "ignore"}, marked by the team in the session notes) win
      over all of the above: "ignore" leaves the lap out of everything; "use" counts it even
      if it's slow or invalid (the team knows better, e.g. a pit-lane "invalid" that wasn't).
      An out or unfinished lap marked "use" counts towards the averages, but still never
      towards lap times, as it isn't a full lap.
    """
    flags = flags or {}
    timed = [l for l in laps if l.kind == "timed"]
    # The yardstick for "slow": the best lap that counts for pace (valid or marked use, not ignored)
    valid_times = [l.lap_time_s for l in timed
                   if flags.get(l.number) != "ignore" and (not l.invalid or flags.get(l.number) == "use")]
    best = min(valid_times, default=None)
    out = []
    for l in laps:
        reason = None
        flag = flags.get(l.number)
        if flag == "ignore":
            reason = "marked ignore"
        elif flag == "use":
            pass
        elif l.kind != "timed":
            reason = "out lap" if l.kind == "out" else "unfinished lap"
        elif f.slow_filter == "percent" and best is not None and l.lap_time_s > best * (1 + f.slow_pct / 100):
            reason = f"slow +{100 * (l.lap_time_s / best - 1):.0f}%"
        elif f.slow_filter == "first_last" and len(timed) >= 3 and (l is timed[0] or l is timed[-1]):
            reason = "first lap" if l is timed[0] else "last lap"
        partial = l.kind != "timed"
        in_averages = reason is None or (partial and f.include_partial and flag != "ignore")
        in_pace = reason is None and not partial and (not l.invalid or flag == "use")
        out.append(l.model_copy(update=dict(excluded=reason, flag=flag, in_averages=in_averages, in_pace=in_pace)))
    return out


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(float(np.mean(values)), 3) if values else None


def stint(s: LdSession, session_id: str, st: MetricSettings | None = None, sector_lines: list[float] | None = None,
          lap_filter: LapFilter | None = None, flags: dict[int, str] | None = None) -> Stint:
    st = st or MetricSettings()
    laps = classify_laps([lap_metrics(s, info, st, sector_lines or []) for info in lap_infos(s)], lap_filter or LapFilter(),
                         flags)
    rep = [l for l in laps if l.in_pace]
    # Fuel and wear happen on every counted lap, valid or not, so average those over all of them
    timed = [l for l in laps if l.in_averages]
    times = [l.lap_time_s for l in rep]
    fuel_per_lap = _mean(l.fuel_used for l in timed)
    fuel_left = laps[-1].fuel_left if laps else None
    rep_splits = [l.splits_s for l in rep if l.splits_s]
    best_splits = [round(min(col), 3) for col in zip(*rep_splits)] if rep_splits else []
    return Stint(
        session_id=session_id, driver=s.driver, car=s.car, track=s.track, laps=laps,
        summary=StintSummary(
            representative_laps=len(rep),
            averaged_laps=len(timed),
            best_lap_s=min(times) if times else None,
            best_splits_s=best_splits,
            theoretical_best_s=round(sum(best_splits), 3) if best_splits else None,
            average_lap_s=_mean(times),
            consistency_s=round(float(np.std(times)), 3) if len(times) >= 2 else None,
            fuel_per_lap=fuel_per_lap,
            fuel_left=fuel_left,
            laps_of_fuel_left=round(fuel_left / fuel_per_lap, 1) if fuel_left is not None and fuel_per_lap else None,
            grip_lost_per_lap={c: _mean(l.grip_lost.get(c) for l in timed) for c in CORNERS if timed and c in timed[0].grip_lost},
            vkm_per_lap={c: _mean(l.vkm.get(c) for l in timed) for c in CORNERS if timed and c in timed[0].vkm},
            kers_per_lap_kj=_mean(l.kers_deployed_kj for l in timed),
            g_abs_avg=_mean(l.g_abs_avg for l in rep),
            g_abs_p95=_mean(l.g_abs_p95 for l in rep),
        ),
    )
