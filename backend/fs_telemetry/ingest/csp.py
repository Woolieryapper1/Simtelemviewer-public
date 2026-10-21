"""Make a MoTeC .ld written by Custom Shaders Patch's own telemetry logger look
like a telemetrick one.

CSP (the AC mod most people run) has a built-in logger that writes .ld/.ldx files
into Documents/Assetto Corsa/telemetry/<track>/<car>/. They are real MoTeC files,
so motec_ld.read_ld reads them, but CSP names and scales channels its own way
and leaves out what the app splits laps with:
  * no "Lap Distance", "Lap Number" or "Lap Time" - only "Lap Progression",
    the share of the lap done (0-100 %), which wraps back to 0 at the
    start/finish line. Only some files have lap beacons in the .ldx, and
    those are ~35 ms after the crossing, so the crossings are always worked
    out from the progression instead
  * other names for the G, position, velocity and angle channels
  * some other units (speed in m/s, ratios where telemetrick has %) and an
    opposite sign on a few channels

So, right after reading, this:
  * finds the line crossings from where the progression wraps, and makes the
    lap channels from them
  * works out the track length, as CSP doesn't log it: roughly from the
    distance driven per lap, then, if the track is installed, the length of its
    AI line (the line the progression is a share of), so every run of a track
    gets the same distances and laps from different runs line up
  * renames/rescales the channels the app uses to telemetrick's names, units
    and signs (MAPPED below); every other channel keeps its CSP name

Signs and axes were checked on real CSP runs (P13b at Road America and
Silverstone, Mercedes 190 at Norisring) against telemetrick runs (Mustang at
Watkins Glen, MFT02). No car/track pair was logged with both, so each sign is
pinned to something that means the same in every file: the left-right tyre
load (the outside tyres carry more), speed changes and ride heights:
  * lateral G and steering already match; CSP's yaw rate has the opposite sign
  * CSP's tyre FX has the opposite sign to telemetrick's "Tire Long Fx"
  * CSP's local velocity is X sideways (+ = -Y in telemetrick), Y up, Z forwards
  * CSP's pitch (Chassis Angle Surface X) is + nose DOWN; telemetrick's is + nose up
  * telemetrick's X/Y map is AC's world mirrored: Car Coord X = -world X,
    Car Coord Y = world Z (from the car's heading, Chassis Angle World Y)
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from ..config import TRACKS_DIRS
from .ac_track import ai_line_length, find_layouts
from .motec_ld import Channel, LdSession

PROGRESSION = "Lap Progression"
# A step bigger than this share of the lap per second can't be driven: it's a
# teleport (back to the pits, a reset), or the progression jumping (now and then it
# steps back ~40 m while the car drives on, perhaps when it's off the track).
MAX_LAPS_PER_S = 0.25
# The logger also notes physics time twice a second. A bigger step than this
# means it missed some running (it stops while the game is paused or loading).
MAX_LOG_GAP_S = 1.0
# Without a full lap, the track length comes from the distance per share of the
# lap driven above this speed (in the pit lane the progression is unreliable),
# once at least FAST_SHARE of a lap has been driven like that.
FAST_MS = 15.0
FAST_SHARE = 0.02
# An installed track's AI line is used if it's within this much of the estimate
# (one lap with an off or two can be ~6% long); otherwise it's probably another
# version of the track, so the estimate is used.
AI_LINE_MATCH = 0.08
# CSP's track-limits flag can still be set just after the line; ignore that much of a lap.
FLAG_GRACE_S = 1.5
# "Back to pits" (or a reset) puts the car at rest somewhere else. A jump along the lap
# that leaves the car slower than this (m/s) is one of those, and ends the lap there.
AT_REST_MS = 1.0
MIN_OUT_LAP = 0.5  # share of a lap

CORNERS = ("FL", "FR", "RL", "RR")


def _same(v):
    return v


def _flip(v):
    return -v


def _percent(v):
    return v * 100


# CSP channel -> (telemetrick name, unit, conversion)
MAPPED = {
    "Ground Speed": ("Ground Speed", "kph", lambda v: v * 3.6),
    "Wind Speed": ("Wind Speed", "kph", lambda v: v * 3.6),
    "Chassis Yaw Rate": ("Chassis Yaw Rate", "deg/s", _flip),
    "G Force Lat": ("CG Accel Lateral", "G", _same),
    "G Force Long": ("CG Accel Longitudinal", "G", _same),
    "G Force Vert": ("CG Accel Vertical", "G", _same),  # like telemetrick's, 0 at rest
    "Chassis World Pos X": ("Car Coord X", "m", _flip),
    "Chassis World Pos Z": ("Car Coord Y", "m", _same),
    "Chassis World Pos Y": ("Car Coord Z", "m", _same),  # height
    "Chassis Angle World Y": ("Car Heading", "deg", lambda v: (v + 180) % 360 - 180),
    "Chassis Linear Velocity Z": ("Chassis Velocity X", "m/s", _same),
    "Chassis Linear Velocity X": ("Chassis Velocity Y", "m/s", _flip),
    "Chassis Linear Velocity Y": ("Chassis Velocity Z", "m/s", _same),
    # Angles to the road surface (not to the world, so hills and banking don't count).
    # Rates are given the signs that make them the rate of change of these angles.
    "Chassis Angle Surface X": ("Chassis Pitch Angle", "deg", _flip),
    "Chassis Angle Surface Z": ("Chassis Roll Angle", "deg", _same),
    "Chassis Angular Velocity X": ("Chassis Pitch Rate", "deg/s", _flip),
    "Chassis Angular Velocity Z": ("Chassis Roll Rate", "deg/s", _flip),
    "Clutch Pos": ("Clutch Pos", "%", _percent),
    "Handbrake Pos": ("Handbrake Pos", "%", _percent),
    "Brake Bias": ("Brake Bias", "%", _percent),
}
for _c in CORNERS:
    MAPPED |= {
        f"Tire FY {_c}": (f"Tire Lat Fy {_c}", "N", _same),
        f"Tire FX {_c}": (f"Tire Long Fx {_c}", "N", _flip),
        f"Tire Slip Ratio {_c}": (f"Tire Slip Ratio {_c}", "%", _percent),
        f"Tire Rubber Grip {_c}": (f"Tire Rubber Grip {_c}", "%", _percent),
        f"Tire Virtual KM {_c}": (f"VirtualKm {_c}", "km", _same),
    }
# The same as Chassis Yaw Rate (before its sign is flipped), so it would only confuse
_DROP = {"Chassis Angular Velocity Y"}


def is_csp_log(s: LdSession) -> bool:
    return PROGRESSION in s.channels and "Lap Distance" not in s.channels


def _laps_done(progress: np.ndarray, freq: int) -> np.ndarray:
    """Laps done since the start of the run (the progression plus a whole lap per
    line crossing), with the logger's stray samples taken out.

    Now and then CSP logs one value that's nowhere near its neighbours, e.g.
    99.97, 29.12, 0.02 % across the line, or 99.96, 99.51, 0.06 %. Left in, the
    first hides a line crossing and both look like a teleport."""
    x = progress.copy()
    limit = 2 * MAX_LAPS_PER_S / freq
    short = lambda a, b: (b - a + 0.5) % 1.0 - 0.5  # noqa: E731 - lap share from a to b, the short way round
    if len(x) >= 3:
        stray = np.flatnonzero((np.abs(short(x[:-2], x[2:])) <= limit) & (np.abs(short(x[:-2], x[1:-1])) > limit)) + 1
        x[stray] = (x[stray - 1] + short(x[stray - 1], x[stray + 1]) / 2) % 1.0  # halfway between its neighbours
    step = short(x[:-1], x[1:])  # across the line that's a small step, not -1 lap
    done = x[0] + np.concatenate([[0.0], np.cumsum(step)])
    if len(done) >= 3:  # small stray values (the second example)
        done[1:-1] = np.median(np.stack([done[:-2], done[1:-1], done[2:]]), axis=0)
    return done


def _estimate_length(step: np.ndarray, ok: np.ndarray, flagged: np.ndarray, driven: np.ndarray,
                     fast: np.ndarray, crossings: np.ndarray, grace: int) -> float | None:
    """Rough metres per lap from the distance driven: the median of the full laps
    (clean ones if there are any), else per share of the lap driven at speed.
    None if the car hardly went anywhere along the lap.

    step/ok/driven/fast are per pair of samples, so lap a..b (samples) is a-1..b-1 in them."""
    laps = [(a, b) for a, b in zip(crossings, crossings[1:]) if ok[a - 1:b - 1].all()]
    clean = [(a, b) for a, b in laps if not flagged[a + grace:b].any()]
    if laps:
        return float(np.median([driven[a - 1:b - 1].sum() for a, b in clean or laps]))
    use = ok & fast & (step > 0)
    share = step[use].sum()
    return float(driven[use].sum() / share) if share > FAST_SHARE else None


def _ai_line_length(track: str, estimate: float, tracks_dirs: list[Path]) -> float | None:
    """The installed track's AI line length, picking the layout closest to the estimate."""
    lengths = []
    for path in find_layouts(tracks_dirs, track).values():
        try:
            lengths.append(ai_line_length(path))
        except (ValueError, OSError, struct.error):
            continue
    best = min(lengths, key=lambda length: abs(length - estimate), default=None)
    return best if best is not None and abs(best - estimate) <= AI_LINE_MATCH * estimate else None


def _lap_flag(n: int, freq: int, edges: list[float], bad: np.ndarray) -> np.ndarray:
    """1 from the moment something goes wrong in a lap until the lap ends."""
    lap_of = np.searchsorted(np.asarray(edges) * freq, np.arange(n), side="right")
    flag = np.zeros(n)
    for k in np.unique(lap_of):
        m = lap_of == k
        flag[m] = np.maximum.accumulate(bad[m])
    return flag


def adapt_csp_log(s: LdSession, tracks_dirs: list[Path] = TRACKS_DIRS) -> LdSession:
    """Rename/rescale CSP's channels and add the lap channels and beacons. Changes s and returns it."""
    prog_ch = s.channels[PROGRESSION]
    freq, n = prog_ch.freq, len(prog_ch.data)
    t = prog_ch.time
    progress = prog_ch.data / 100.0

    speed = s.channels.get("Ground Speed")  # m/s at this point
    v = np.interp(t, speed.time, speed.data) if speed is not None else np.zeros(n)
    driven = v[1:] / freq  # metres covered between each pair of samples

    done = _laps_done(progress, freq)
    progress = done % 1.0
    step = np.diff(done)

    # Something a lap time can't be trusted over: a jump along the lap or the logger
    # missing some running. A jump that leaves the car at rest is a teleport (back to
    # the pits, a reset), which ends the lap: the lap so far is unfinished and an out lap starts.
    odd = np.abs(step) > MAX_LAPS_PER_S / freq
    teleport = odd & (v[1:] < AT_REST_MS)
    starts = np.flatnonzero(teleport)
    starts = starts[np.diff(starts, prepend=-np.inf) > freq]  # one jump can take a couple of samples
    breaks = [float(k + 1) / freq for k in starts]
    phys = s.channels.get("Time Phys")
    if phys is not None and len(phys.data) > 1:
        gaps = np.flatnonzero(np.diff(phys.data) > 1.0 / phys.freq + MAX_LOG_GAP_S) + 1
        odd[np.clip(np.round(gaps / phys.freq * freq).astype(int) - 1, 0, max(n - 2, 0))] = True

    # A lap starts when the car first gets onto a new lap by driving there. Reversing
    # back over the line and driving over it again isn't a new lap, nor is a teleport.
    # Counted afresh after each teleport, and a pit box can be just before the line,
    # so the out lap only ends at a crossing after driving at least half a lap.
    furthest, driven_since = np.floor(done), np.zeros(n)
    for a, b in zip([0, *(starts + 1)], [*(starts + 1), n]):
        furthest[a:b] = np.maximum.accumulate(furthest[a:b])
        driven_since[a:b] = done[a:b] - done[a]
    crossings = np.flatnonzero((furthest[1:] > furthest[:-1]) & ~odd & (driven_since[1:] >= MIN_OUT_LAP)) + 1  # first sample after each
    # Where in between the two samples the line was: lap times to ~1 ms, not 1/freq
    before = furthest[crossings] - done[crossings - 1]
    fraction = before / np.maximum(done[crossings] - done[crossings - 1], 1e-9)
    beacons = [float(x) for x in (crossings - 1 + fraction) / freq]
    # CSP's own flag (AC's track limits, logged twice a second), on this clock
    flag = s.channels.get("Lap Invalidated")
    flagged = (flag.data[np.clip(np.searchsorted(flag.time, t, side="right") - 1, 0, len(flag.data) - 1)] > 0
               if flag is not None else np.zeros(n, dtype=bool))

    estimate = _estimate_length(step, ~odd, flagged, driven, v[1:] > FAST_MS, crossings, round(FLAG_GRACE_S * freq))
    ai_line = _ai_line_length(s.track, estimate, tracks_dirs) if estimate else None
    length = ai_line or estimate

    def add(name: str, unit: str, values: np.ndarray, description: str, rate: int = freq) -> None:
        s.channels[name] = Channel(name, unit, rate, np.asarray(values, dtype=np.float64), description=description)

    # Renames: build the new channels first, then drop the CSP originals
    renamed = {}
    for csp_name, (name, unit, convert) in MAPPED.items():
        ch = s.channels.get(csp_name)
        if ch is not None:
            renamed[name] = Channel(name, unit, ch.freq, convert(ch.data), description=f"CSP logger's {csp_name}")
    for csp_name in [*MAPPED, *_DROP]:
        s.channels.pop(csp_name, None)
    s.channels.update(renamed)
    for ch in s.channels.values():
        if ch.unit == "°C":
            ch.unit = "C"  # as telemetrick writes it

    for axle, (a, b) in (("Front", ("FL", "FR")), ("Rear", ("RL", "RR"))):
        if f"Ride Height {a}" in s.channels and f"Ride Height {b}" in s.channels:
            ra, rb = s.channels[f"Ride Height {a}"], s.channels[f"Ride Height {b}"]
            add(f"Ride Height {axle}", ra.unit, (ra.data + rb.data) / 2,
                f"Average of the two {axle.lower()} ride heights", rate=ra.freq)

    if length:
        lap_distance = progress * length
        how = (f"CSP's lap progression x {length:.0f} m, the length of the track's AI line" if ai_line else
               f"CSP's lap progression x {length:.0f} m, the track length worked out from the distance driven")
    else:  # the car never went far along the lap (e.g. a track with no AI line): distance driven
        lap_distance = np.concatenate([[0.0], np.cumsum(driven)])
        how = "Distance driven since the start of the run (CSP's lap progression hardly moved)"
    lap_number = np.searchsorted(np.asarray(beacons) * freq, np.arange(n), side="right")
    edges = sorted({*beacons, *breaks})
    lap_start = np.concatenate([[0.0], edges])[np.searchsorted(np.asarray(edges) * freq, np.arange(n), side="right")]
    add("Lap Distance", "m", lap_distance, how)
    add("Lap Number", "", lap_number, "Line crossings so far (worked out from CSP's lap progression)")
    add("Lap Time", "s", t - lap_start, "Time since the lap started (worked out from CSP's lap progression)")
    add("Car Pos Norm", "", progress, "Share of the lap done, 0-1 (CSP's lap progression)")
    add("Lap Invalidated", "", np.maximum(flagged, _lap_flag(n, freq, edges, np.insert(odd & ~teleport, 0, False))),
        "1 once the lap is invalid: AC's track limits (CSP's flag), a jump along the lap or a gap in the log")

    s.beacons, s.breaks = beacons, breaks
    if length:
        s.details.setdefault("Venue Length", str(round(length, 1)))
    return s
