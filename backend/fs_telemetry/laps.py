"""Lap-level analysis: classify laps, and put channels onto lap distance.

Why distance and not time? To compare two laps you want "what was the driver
doing at turn 3", not "what was the driver doing 20 s in". A faster lap reaches
turn 3 sooner, so on a time axis the two laps drift apart; on a distance axis
they line up corner by corner.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .ingest.motec_ld import Lap, LdSession

DISTANCE_CHANNEL = "Lap Distance"
LAP_TIME_CHANNEL = "Lap Time"  # AC's own lap timer, logged alongside distance
INVALID_CHANNEL = "Lap Invalidated"
# The invalid flag is only logged once per second, so the first sample of a lap
# can still carry the previous lap's flag. Ignore that much of each lap.
INVALID_FLAG_GRACE_S = 1.5


@dataclass
class LapInfo:
    number: int
    start: float
    lap_time: float
    kind: str  # "out", "timed" or "incomplete"
    invalid: bool


def lap_infos(s: LdSession) -> list[LapInfo]:
    laps = s.laps
    breaks = set(s.breaks)
    inv = s.channels.get(INVALID_CHANNEL)
    out = []
    for i, lap in enumerate(laps):
        if i == len(laps) - 1 or lap.end in breaks:
            kind = "incomplete"  # no beacon at the end: session stopped (or was cut off) mid-lap
        elif i == 0 or lap.start in breaks:
            kind = "out"
        else:
            kind = "timed"
        invalid = False
        if inv is not None:
            m = (inv.time >= lap.start + INVALID_FLAG_GRACE_S) & (inv.time < lap.end)
            invalid = bool(np.any(inv.data[m] > 0))
        out.append(LapInfo(lap.number, lap.start, lap.lap_time, kind, invalid))
    return out


def best_lap(infos: list[LapInfo]) -> LapInfo | None:
    valid = [l for l in infos if l.kind == "timed" and not l.invalid]
    return min(valid, key=lambda l: l.lap_time, default=None)


def track_length(s: LdSession) -> float:
    return float(s.channels[DISTANCE_CHANNEL].data.max())


def _strictly_rising(x: np.ndarray) -> np.ndarray:
    """True where a value is smaller than every value after it.

    The logger sometimes writes a stale sample (a value from a moment earlier),
    e.g. lap time 5.06, 5.00, 5.06, 5.11. Real time and distance only go
    forwards, so any sample that a later sample "undercuts" is stale.
    """
    min_after = np.minimum.accumulate(x[::-1])[::-1]  # min of x[i:]
    return x < np.append(min_after[1:], np.inf)


def _longest_run_between_resets(lap_time: np.ndarray) -> slice:
    """Split where the lap timer jumps back (a reset) and keep the longest piece."""
    cuts = np.flatnonzero(np.diff(lap_time) < -1.0) + 1
    edges = [0, *cuts, len(lap_time)]
    start, end = max(zip(edges, edges[1:]), key=lambda e: e[1] - e[0])
    return slice(start, end)


def _lap_distance(
    s: LdSession, lap: Lap, has_end_beacon: bool
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For every good sample in a lap: session time, lap time, and clean
    0..track_length distance.

    The raw "Lap Distance" channel resets to 0 at the start/finish line, but the
    reset is logged a sample or two late, and an out lap starts part-way round
    (in the pit lane). So we: undo the resets, anchor the lap so it ends at the
    line, and drop anything outside 0..track_length.

    The logger's sample spacing isn't perfectly even, so "sample i is at i/20 s"
    is only roughly true. For lap time we use AC's own "Lap Time" channel, which
    is recorded in the same sample as the distance, so each pair is consistent.
    """
    ch = s.channels[DISTANCE_CHANNEL]
    L = track_length(s)
    m = (ch.time >= lap.start) & (ch.time <= lap.end)
    t, d = ch.time[m], ch.data[m].copy()
    lap_clock = s.channels.get(LAP_TIME_CHANNEL)
    if lap_clock is not None and len(lap_clock.data) == len(ch.data):
        lt = lap_clock.data[m]
        # The lap timer resets to 0 at each line crossing, and the window can
        # catch a sample either side of a reset. Keep only this lap's stretch.
        run = _longest_run_between_resets(lt)
        t, d, lt = t[run], d[run], lt[run]
    else:  # fall back to the logger's clock
        lt = t - lap.start

    # 1. Drop stale samples FIRST. A stale sample can carry an old distance from
    #    the other side of the line (e.g. 0.8 m, then 1643 m again), which would
    #    look like an extra reset in step 2 and shift the whole lap.
    fresh = _strictly_rising(lt)
    t, lt, d = t[fresh], lt[fresh], d[fresh]
    if len(d) < 2:
        return t, lt, d

    # 2. Undo resets: after each big drop (~L to ~0), add a track length.
    drops = np.diff(d) < -L / 2
    d += np.concatenate([[0], np.cumsum(drops)]) * L

    # 3. Shift by whole laps so the lap lines up with the line.
    if has_end_beacon:
        d -= np.round((d[-1] - L) / L) * L  # finishes at ~L
    else:
        d -= np.round(d[0] / L) * L  # starts at ~0

    # 4. Keep 0..L, and force distance to only go forwards.
    keep = (d >= 0) & (d <= L)
    return t[keep], lt[keep], np.maximum.accumulate(d[keep])


def _is_discrete(values: np.ndarray, unit: str) -> bool:
    """Gear, flags, lap number... must not be blended (no "gear 3.5").
    Spot them as unit-less channels that only ever hold whole numbers."""
    return unit == "" and bool(np.all(np.mod(values, 1) == 0))


def resample_lap(
    s: LdSession, lap_number: int, channel_names: list[str], step_m: float = 1.0
) -> dict[str, np.ndarray]:
    """Channels for one lap on an even distance grid (every `step_m` metres).

    Returns {"Distance": ..., "Time": ..., <channel>: ...}, all the same length.
    "Time" is seconds since the lap started, which later gives us time deltas.
    """
    laps = s.laps
    lap = laps[lap_number - 1]
    t, lt, d = _lap_distance(s, lap, has_end_beacon=lap_number < len(laps) and lap.end not in s.breaks)
    if len(d) < 2:
        return {"Distance": np.array([]), "Time": np.array([])}

    grid = np.arange(np.ceil(d[0] / step_m) * step_m, d[-1], step_m)
    # Tiny ramp makes distance strictly increasing, which np.interp needs
    # (e.g. while stationary in the pits distance doesn't change).
    d_rising = d + np.arange(len(d)) * 1e-9
    t_grid = np.interp(grid, d_rising, t)  # where to read the other channels

    out = {"Distance": grid, "Time": np.interp(grid, d_rising, lt)}
    for name in channel_names:
        ch = s.channels[name]
        if _is_discrete(ch.data, ch.unit):
            idx = np.clip(np.searchsorted(ch.time, t_grid, side="right") - 1, 0, len(ch.data) - 1)
            out[name] = ch.data[idx]
        else:
            out[name] = np.interp(t_grid, ch.time, ch.data)
    return out
