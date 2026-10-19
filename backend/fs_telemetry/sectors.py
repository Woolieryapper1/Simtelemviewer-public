"""Official sector splits, as AC times them.

AC logs "Last Sector Time": each time the car crosses a sector line, the channel
changes to the time of the sector just finished. It's only logged once a second,
so we can't see exactly *where* the lines are from it - but we can work it out:

    sector 1 took 24.731 s  ->  find where the lap's clock read 24.731 s
                            ->  that distance is the end of sector 1

Doing this for every lap on the track and taking the median gives each sector
line to within a metre or so. Then ANY lap (even ones where the channel is
missing) can be split at the same lines, using its time-vs-distance curve.

iRacing files are easier: their session info says where the sector lines are
(session.sector_lines_m, see ingest/ibt.py), so those are used as they are.
"""

from __future__ import annotations

import numpy as np

from .laps import lap_infos, resample_lap, track_length

SECTOR_CHANNEL = "Last Sector Time"
GRACE_S = 1.5  # ignore a change right after the line - that's the previous lap's last sector


def _lap_sector_ends(s, info) -> list[float]:
    """Distances where this lap's sectors ended, from AC's sector times."""
    ch = s.channels.get(SECTOR_CHANNEL)
    if ch is None:
        return []
    lap = s.laps[info.number - 1]
    idx = np.flatnonzero((ch.time > lap.start + GRACE_S) & (ch.time < lap.end))
    if not len(idx):
        return []
    values = ch.data[idx]
    previous = np.concatenate([[ch.data[idx[0] - 1] if idx[0] > 0 else np.nan], values[:-1]])
    changes = values[values != previous]  # a new value = a sector just finished
    if not len(changes):
        return []
    r = resample_lap(s, info.number, [], step_m=2.0)
    if len(r["Distance"]) < 2:
        return []
    ends = np.cumsum(changes)  # lap clock at the end of each sector
    if ends[-1] >= info.lap_time:
        return []
    return [float(np.interp(t, r["Time"], r["Distance"])) for t in ends]


_cache: dict[tuple, tuple[list[float], str]] = {}


def _compute(catalog, track: str, ids: list[str]) -> tuple[list[float], str]:
    per_count: dict[int, list[list[float]]] = {}
    sessions = [catalog.get(i) for i in ids]
    given: dict[int, list[list[float]]] = {}
    for s in sessions:
        if s.sector_lines_m:
            given.setdefault(len(s.sector_lines_m), []).append(s.sector_lines_m)
    if given:  # (grouped by count in case iRacing changed the sectors in a track update)
        lines = max(given.values(), key=len)
        return [round(float(x), 1) for x in np.median(np.array(lines), axis=0)], "iRacing sector lines (from the session info)"
    for s in sessions:
        for info in lap_infos(s):
            if info.kind == "timed":
                ends = _lap_sector_ends(s, info)
                if ends:
                    per_count.setdefault(len(ends), []).append(ends)
    if per_count:
        # the most common number of sector lines wins (normally 2 lines = 3 sectors)
        laps = max(per_count.values(), key=len)
        return [round(float(x), 1) for x in np.median(np.array(laps), axis=0)], f"AC sector timing, {len(laps)} laps"
    L = track_length(sessions[0])
    return [round(L / 3, 1), round(2 * L / 3, 1)], "equal thirds (no AC sector timing in these files)"


def sector_boundaries(catalog, track: str) -> tuple[list[float], str]:
    """Distances of the sector lines for a track, and where they came from.
    Remembered per set of files, so new exports are picked up automatically."""
    ids = sorted(x.id for x in catalog.summaries() if x.track == track)
    if not ids:
        return [], "no sessions"
    key = (track, tuple(ids))
    if key not in _cache:
        _cache[key] = _compute(catalog, track, ids)
    return _cache[key]


def split_times(r: dict, boundaries: list[float], lap_time: float) -> list[float]:
    """Sector times for one lap resampled on distance (see laps.resample_lap)."""
    if len(r["Distance"]) < 2:
        return []
    at = [float(np.interp(b, r["Distance"], r["Time"])) for b in boundaries]
    edges = [0.0, *at, lap_time]
    return [round(b - a, 3) for a, b in zip(edges, edges[1:])]
