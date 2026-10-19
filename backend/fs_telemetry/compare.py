"""Compare laps mini-sector by mini-sector ("track dominance", as on F1 TV).

The lap is cut into N equal stretches of track. For each stretch we work out
how long every lap took to get through it, and which lap was quickest. Adding
up the quickest time in every stretch gives the "theoretical best" lap.

Times come from the lap's Time-vs-Distance curve (see laps.py), so a
mini-sector time is simply  time at its end  minus  time at its start.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pydantic import BaseModel

from .catalog import Catalog
from .config import TRACKS_DIRS
from .ingest.ac_track import AlignedTrack, align_to_lap, find_layouts, read_fast_lane
from .ingest.motec_ld import LdSession
from .laps import LapInfo, lap_infos, resample_lap, track_length
from .sectors import sector_boundaries, split_times

MAP_CHANNELS = ["Car Coord X", "Car Coord Y", "Ground Speed"]
MAP_STEP_M = 2.0


class CompareError(ValueError):
    """A comparison that can't be done (shown to the user as a 422)."""


@dataclass
class LapRef:
    session_id: str
    lap: int

    @classmethod
    def parse(cls, text: str) -> "LapRef":
        """'<session id>:<lap number>' -> LapRef"""
        session_id, _, lap = text.rpartition(":")
        if not session_id or not lap.isdigit():
            raise CompareError(f"'{text}' should look like <session id>:<lap number>")
        return cls(session_id, int(lap))


class LapResult(BaseModel):
    session_id: str
    lap: int
    driver: str
    lap_time_s: float
    invalid: bool
    sector_times_s: list[float]
    splits_s: list[float]  # official sector times (S1, S2, S3...)
    sectors_won: int
    time_in_won_sectors_s: float  # how much quicker than the next-best lap, summed over sectors it won


class MiniSector(BaseModel):
    number: int
    start_m: float
    end_m: float
    winner: int  # index into `laps`
    margin_s: float  # winner's advantage over the next-best lap (0 if only one lap)


class TrackMap(BaseModel):
    """Racing line of the first (reference) lap, every MAP_STEP_M metres."""

    x: list[float]
    y: list[float]
    distance: list[float]
    speed: list[float]


class RacingLine(BaseModel):
    """Where the car actually went: raw 30 Hz positions, for zooming in on lines."""

    x: list[float]
    y: list[float]
    distance: list[float]
    throttle: list[float] | None = None  # pedal % at each point (None if the file doesn't have it)
    brake: list[float] | None = None


class TrackOutline(BaseModel):
    """Track edges from the AC track's fast_lane.ai, in the telemetry's coordinates."""

    layout: str
    median_gap_m: float  # typical distance between our lap and AC's AI line - a sanity check
    left_x: list[float]
    left_y: list[float]
    right_x: list[float]
    right_y: list[float]


class Comparison(BaseModel):
    track: str
    track_length_m: float
    laps: list[LapResult]
    sectors: list[MiniSector]
    theoretical_best_s: float  # best of every mini-sector
    sector_lines_m: list[float]  # where the official sectors end (inside the lap)
    sector_source: str  # how the sector lines were found
    best_splits_s: list[float]  # quickest time in each official sector among these laps
    theoretical_best_sectors_s: float  # sum of best_splits_s
    map: TrackMap
    lines: list[RacingLine]  # one per lap, same order as `laps`
    outline: TrackOutline | None
    outline_note: str  # why there's no outline, if there isn't one


def _racing_line(s: LdSession, lap_number: int, grid: dict) -> RacingLine:
    lap = s.laps[lap_number - 1]
    X, Y = s.channels["Car Coord X"], s.channels["Car Coord Y"]
    m = (X.time >= lap.start) & (X.time <= lap.end)
    # distance of each sample, via its time into the lap
    dist = np.interp(X.time[m] - lap.start, grid["Time"], grid["Distance"])
    r = lambda a: [round(float(v), 2) for v in a]
    # pedals at the same moments as the positions, for colouring the line by what the driver was doing
    pedal = lambda name: r(np.interp(X.time[m], s.channels[name].time, s.channels[name].data)) if name in s.channels else None
    return RacingLine(x=r(X.data[m]), y=r(Y.data[m]), distance=r(dist),
                      throttle=pedal("Throttle Pos"), brake=pedal("Brake Pos"))


# Track outlines are slow-ish to line up (tries every layout and axis mapping),
# so remember the answer per track for as long as the server runs.
_outline_cache: dict[str, tuple[TrackOutline | None, str]] = {}


def _outline(track: str, line: RacingLine) -> tuple[TrackOutline | None, str]:
    if track in _outline_cache:
        return _outline_cache[track]
    files = find_layouts(TRACKS_DIRS, track)
    if not files:
        result = (None, f"No AC track files for '{track}' on the server. Copy the track folder into {TRACKS_DIRS[0]} to draw the track edges.")
    else:
        layouts = {}
        for name, path in files.items():
            try:
                layouts[name] = read_fast_lane(path, name)
            except ValueError:
                continue  # an old/unsupported AI file; try the others
        aligned: AlignedTrack | None = align_to_lap(layouts, np.array(line.x), np.array(line.y)) if layouts else None
        if aligned is None:
            result = (None, f"Found AC files for '{track}' but they don't line up with this telemetry.")
        else:
            r = lambda a: [round(float(v), 2) for v in a]
            result = (TrackOutline(layout=aligned.layout, median_gap_m=aligned.median_gap_m,
                                   left_x=r(aligned.left_x), left_y=r(aligned.left_y),
                                   right_x=r(aligned.right_x), right_y=r(aligned.right_y)), "")
    _outline_cache[track] = result
    return result


def _time_at(boundaries: np.ndarray, distance: np.ndarray, time: np.ndarray, lap_time: float) -> np.ndarray:
    """Lap time on reaching each boundary. The line itself is exactly 0 and lap_time."""
    t = np.interp(boundaries, distance, time)
    t[0], t[-1] = 0.0, lap_time
    return t


def load_timed_laps(catalog: Catalog, refs: list[LapRef]) -> list[tuple[LapRef, LdSession, LapInfo]]:
    """Look up laps for comparing: they must exist, be full timed laps, and share a track."""
    if not refs:
        raise CompareError("Pick at least one lap")
    loaded = []
    for ref in refs:
        s = catalog.get(ref.session_id)
        if s is None:
            raise CompareError(f"No session '{ref.session_id}'")
        infos = lap_infos(s)
        if not 1 <= ref.lap <= len(infos):
            raise CompareError(f"Lap {ref.lap} not found in '{ref.session_id}'")
        info = infos[ref.lap - 1]
        if info.kind != "timed":
            raise CompareError(f"Lap {ref.lap} of {s.driver}'s session is an {info.kind} lap; pick a full timed lap")
        loaded.append((ref, s, info))
    tracks = {s.track for _, s, _ in loaded}
    if len(tracks) > 1:
        raise CompareError(f"Laps are from different tracks: {', '.join(sorted(tracks))}")
    return loaded


def compare_laps(catalog: Catalog, refs: list[LapRef], n_sectors: int) -> Comparison:
    loaded = load_timed_laps(catalog, refs)
    for _, s, _ in loaded:
        missing = [c for c in MAP_CHANNELS if c not in s.channels]
        if missing:  # e.g. a CSV without GPS
            raise CompareError(f"{s.driver}'s run has no {' or '.join(missing)} (GPS positions and speed), "
                               "so it can't be drawn on the track map")
    first = loaded[0][1]
    L = track_length(first)
    boundaries = np.linspace(0.0, L, n_sectors + 1)

    # Time through every mini-sector, for every lap: shape (laps, sectors)
    resampled = []
    times = []
    for ref, s, info in loaded:
        r = resample_lap(s, ref.lap, MAP_CHANNELS, MAP_STEP_M)
        resampled.append(r)
        times.append(np.diff(_time_at(boundaries, r["Distance"], r["Time"], info.lap_time)))
    times = np.array(times)

    winners = times.argmin(axis=0)
    best = times.min(axis=0)
    second = np.sort(times, axis=0)[1] if len(times) > 1 else best
    margins = second - best

    lines_m, sector_source = sector_boundaries(catalog, first.track)
    splits = [split_times(r, lines_m, info.lap_time) for r, (_, _, info) in zip(resampled, loaded)]
    best_splits = [round(min(col), 3) for col in zip(*splits)] if all(splits) else []

    laps = []
    for i, (ref, s, info) in enumerate(loaded):
        won = winners == i
        laps.append(LapResult(
            session_id=ref.session_id, lap=ref.lap, driver=s.driver,
            lap_time_s=round(info.lap_time, 3), invalid=info.invalid, splits_s=splits[i],
            sector_times_s=[round(float(t), 3) for t in times[i]],
            sectors_won=int(won.sum()) if len(times) > 1 else 0,
            time_in_won_sectors_s=round(float(margins[won].sum()), 3),
        ))

    sectors = [
        MiniSector(number=k + 1, start_m=round(float(boundaries[k]), 1), end_m=round(float(boundaries[k + 1]), 1),
                   winner=int(winners[k]), margin_s=round(float(margins[k]), 3))
        for k in range(n_sectors)
    ]

    lines = [_racing_line(s, ref.lap, r) for (ref, s, _), r in zip(loaded, resampled)]
    if first.source == "iracing":  # iRacing's track files are encrypted, so there are no edges to read
        outline, outline_note = None, "iRacing doesn't make its track edges available, so only the driven lines are drawn."
    else:
        outline, outline_note = _outline(first.track, lines[0])

    m = resampled[0]
    return Comparison(
        lines=lines,
        outline=outline,
        outline_note=outline_note,
        track=first.track,
        track_length_m=round(L, 1),
        laps=laps,
        sectors=sectors,
        theoretical_best_s=round(float(best.sum()), 3),
        sector_lines_m=lines_m, sector_source=sector_source, best_splits_s=best_splits,
        theoretical_best_sectors_s=round(sum(best_splits), 3),
        map=TrackMap(**{k: [round(float(v), 2) for v in m[ch]] for k, ch in
                        [("x", "Car Coord X"), ("y", "Car Coord Y"), ("distance", "Distance"), ("speed", "Ground Speed")]}),
    )
