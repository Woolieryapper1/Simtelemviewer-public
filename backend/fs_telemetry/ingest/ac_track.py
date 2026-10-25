"""Read an Assetto Corsa track's AI line (ai/fast_lane.ai) to draw the track.

fast_lane.ai is the line the AI drivers follow. For every point (~1.4 m apart)
it stores the position and, importantly, how far the left and right track edges
are from it. Offsetting the line sideways by those amounts gives both edges.

Binary layout (version 7), worked out from the files and community tools:
  header:  version, point count, lap time, sample count       (4 x int32)
  points:  x, y (height), z, distance along line, id           (per point: 4 floats + int32)
  extras:  count (int32), then 18 floats per point:
           speed, gas, brake, (obsolete), radius, side_left, side_right, camber,
           direction, normal x/y/z, length, forward x/y/z, tag, grade
  ...followed by a lookup grid we don't need.

Which side is "left": checked on every installed circuit - at corner apexes the
racing line hugs the inside edge, and that only works out if, seen from above
in the x-z plane, side_left is on the clockwise side of the direction of travel.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import config

_POINT = np.dtype([("pos", "<f4", 3), ("length", "<f4"), ("id", "<i4")])
_EXTRA_FLOATS = 18
_SIDE_LEFT, _SIDE_RIGHT, _SPEED = 5, 6, 0


@dataclass
class TrackGeometry:
    """Everything in AC world coordinates: x and z are the ground plane."""

    layout: str  # e.g. "micevec_vfsaa/vfsaa25"
    x: np.ndarray  # AI racing line
    z: np.ndarray
    distance: np.ndarray
    left_x: np.ndarray  # track edges
    left_z: np.ndarray
    right_x: np.ndarray
    right_z: np.ndarray

    @property
    def length(self) -> float:
        return float(self.distance[-1])


def read_fast_lane(path: Path, layout: str = "") -> TrackGeometry:
    buf = Path(path).read_bytes()
    version, n = struct.unpack_from("<2i", buf, 0)
    if version != 7:
        raise ValueError(f"{path}: AI line version {version} not supported (expected 7)")
    pts = np.frombuffer(buf, dtype=_POINT, count=n, offset=16)
    offset = 16 + n * _POINT.itemsize
    n_extra = struct.unpack_from("<i", buf, offset)[0]
    if n_extra != n:
        raise ValueError(f"{path}: has no track-width data")
    extra = np.frombuffer(buf, dtype="<f4", count=n * _EXTRA_FLOATS, offset=offset + 4).reshape(n, _EXTRA_FLOATS)

    x, z = pts["pos"][:, 0].astype(float), pts["pos"][:, 2].astype(float)
    # Direction of travel from the points themselves (some files store a zero
    # forward vector). np.gradient looks one point either side.
    hx, hz = np.gradient(x), np.gradient(z)
    norm = np.hypot(hx, hz)
    norm[norm == 0] = 1.0
    hx, hz = hx / norm, hz / norm
    # "Left" is the clockwise side in the x-z plane: (hz, -hx). See module docstring.
    lx, lz = hz, -hx
    side_left, side_right = extra[:, _SIDE_LEFT].astype(float), extra[:, _SIDE_RIGHT].astype(float)
    return TrackGeometry(
        layout=layout, x=x, z=z, distance=pts["length"].astype(float),
        left_x=x + lx * side_left, left_z=z + lz * side_left,
        right_x=x - lx * side_right, right_z=z - lz * side_right,
    )


def ai_line_length(path: Path) -> float:
    """Length of the AI line (m). Reads only the points, so it also works on files
    whose track-width data read_fast_lane can't use."""
    buf = Path(path).read_bytes()
    version, n = struct.unpack_from("<2i", buf, 0)
    if version != 7 or n < 2:
        raise ValueError(f"{path}: not a version-7 AI line")
    return float(np.frombuffer(buf, dtype=_POINT, count=n, offset=16)["length"][-1])


def find_layouts(tracks_dirs: list[Path], track: str) -> dict[str, Path]:
    """All fast_lane.ai files for a track: {"track" or "track/layout": path}.
    Tracks with layouts keep them in sub-folders: <track>/<layout>/ai/fast_lane.ai"""
    found = {}
    for root in tracks_dirs:
        base = root / track
        if (base / "ai" / "fast_lane.ai").exists():
            found[track] = base / "ai" / "fast_lane.ai"
        if base.is_dir():
            for f in sorted(base.glob("*/ai/fast_lane.ai")):
                found.setdefault(f"{track}/{f.parent.parent.name}", f)
    return found


# --- One name per track ---------------------------------------------------
# Not every logger names a track the same way in the files it writes: one CSP
# writes the track folder ("sportsland_sugo_mts"), another the folder with the
# layout stuck on the end ("sportsland_sugo_mtssugo", the "sugo" layout). Two
# names would make one track look like two, and their runs couldn't be compared.

ALIASES_FILE = config.DATA_DIR / "track_aliases.json"


def track_aliases(path: Path | None = None) -> dict[str, str]:
    """Hand-made renames from the data folder, {"name in the file": "track folder"},
    for a track that isn't installed on this PC. None there (or unreadable): {}."""
    try:
        data = json.loads((path or ALIASES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def canonical_track(track: str, tracks_dirs: list[Path], aliases: dict[str, str]) -> str:
    """The name runs of a track are filed under: its folder name.

    In order: a hand-made alias, an installed track folder of that name, an
    installed track folder followed straight by one of its layouts (a sub-folder
    with its own data/, so not ui/ or extension/). Anything else stays as the file
    has it - a layout name alone isn't enough to go on, as "micevec_vfsaa" with
    its "vfsaa" layout shows."""
    if track in aliases:
        return aliases[track]
    if not track or any((root / track).is_dir() for root in tracks_dirs):
        return track
    for root in tracks_dirs:
        if not root.is_dir():
            continue
        for folder in root.iterdir():
            layout = track[len(folder.name):]
            if track.startswith(folder.name) and layout and (folder / layout / "data").is_dir():
                return folder.name
    return track


# --- Matching the track to the telemetry's coordinates ---------------------
# The telemetry's "Car Coord X/Y" are AC world coordinates too, but the logger
# may name/flip the axes differently. Rather than guess, try each way of
# mapping (x, z) onto (X, Y) and keep the one where a real lap sits on the AI line.

_MAPPINGS = {
    # name: function (x, z) -> (X, Y)
    "x,z": lambda x, z: (x, z),
    "x,-z": lambda x, z: (x, -z),
    "-x,z": lambda x, z: (-x, z),
    "-x,-z": lambda x, z: (-x, -z),
    "z,x": lambda x, z: (z, x),
    "z,-x": lambda x, z: (z, -x),
    "-z,x": lambda x, z: (-z, x),
    "-z,-x": lambda x, z: (-z, -x),
}


def _median_gap(line_x, line_y, lap_x, lap_y) -> float:
    """Median distance from each lap point to the nearest line point."""
    pick = np.linspace(0, len(lap_x) - 1, min(300, len(lap_x))).astype(int)
    px, py = lap_x[pick][:, None], lap_y[pick][:, None]
    d2 = (px - line_x[None, :]) ** 2 + (py - line_y[None, :]) ** 2
    return float(np.median(np.sqrt(d2.min(axis=1))))


@dataclass
class AlignedTrack:
    layout: str
    mapping: str
    median_gap_m: float  # how far the lap typically sits from the AI line - small = good match
    line_x: np.ndarray
    line_y: np.ndarray
    left_x: np.ndarray
    left_y: np.ndarray
    right_x: np.ndarray
    right_y: np.ndarray


def align_to_lap(layouts: dict[str, TrackGeometry], lap_x: np.ndarray, lap_y: np.ndarray) -> AlignedTrack | None:
    """Pick the layout + axis mapping that best fits a lap's X/Y. None if nothing fits."""
    best = None
    for name, g in layouts.items():
        for mapping, f in _MAPPINGS.items():
            lx, ly = f(g.x, g.z)
            gap = _median_gap(lx, ly, lap_x, lap_y)
            if best is None or gap < best[0]:
                best = (gap, name, mapping)
    if best is None or best[0] > 15.0:  # more than ~a track width away: wrong track
        return None
    gap, name, mapping = best
    g, f = layouts[name], _MAPPINGS[mapping]
    (line_x, line_y), (left_x, left_y), (right_x, right_y) = f(g.x, g.z), f(g.left_x, g.left_z), f(g.right_x, g.right_z)
    return AlignedTrack(name, mapping, round(gap, 2), line_x, line_y, left_x, left_y, right_x, right_y)
