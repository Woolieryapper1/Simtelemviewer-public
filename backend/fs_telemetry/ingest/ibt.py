"""Read iRacing telemetry files (.ibt).

iRacing writes one .ibt each time you drive (from getting in the car to getting
out) into Documents/iRacing/telemetry. Unlike MoTeC's .ld, the format is
published in iRacing's own SDK (irsdk_defines.h), so nothing here is guesswork.

Layout of a .ibt file, in plain terms:
  1. A fixed header: ticks per second (60), where the channel list is, where
     the session info text is, and where the samples start.
  2. A small "disk" header: the date and how many samples were written.
  3. The channel list: for each channel its name, unit, description, type
     (whole number, decimal...) and where it sits inside one sample.
  4. The session info: a YAML text block with the track, the drivers, the
     sector lines and the full car setup.
  5. The samples: one fixed-size record per tick, every channel side by side.
     Every channel has the same rate, so the whole lot reads in one go as a
     numpy table with a column per channel.

On top of reading, this makes an iRacing run look like an AC one to the rest of
the app, so every page and maths channel works unchanged:
  * channels the app uses get telemetrick's AC names and units (MAPPED below)
  * GPS latitude/longitude become x/y metres for the track map
  * lap beacons come from iRacing's lap counter (the .ibt has no beacon list)
  * a "Lap Invalidated" flag is built, as iRacing doesn't log one
  * every other channel that changes during the run is kept under its iRacing name
"""

from __future__ import annotations

import struct
from datetime import datetime
from pathlib import Path

import numpy as np

from .motec_ld import Channel, LdSession, _text

G = 9.81
EARTH_RADIUS_M = 6_371_000.0
KPA_TO_PSI = 0.1450377

# --- Binary layouts -------------------------------------------------------
# Header (first 48 bytes): version, status, tick rate, session info update count,
# session info length + position, channel count, channel list position,
# buffer count, bytes per sample. Then the first buffer's (tick count, position).
_HEADER = struct.Struct("<10i")
_DATA_POS = struct.Struct("<4xi")  # at byte 48
# Disk header (at byte 112): start date (unix time), start/end session time, lap count, sample count
_DISK = struct.Struct("<qddii")
# One channel description: type, position in the sample, array length, (flag), name, description, unit
_VAR = struct.Struct("<iii?3x32s64s32s")
# iRacing type number -> numpy type. 0 = text, which we skip.
_TYPES = {1: "?", 2: "<i4", 3: "<u4", 4: "<f4", 5: "<f8"}

# --- Channel mapping ------------------------------------------------------
# iRacing channel -> (app channel name, unit, conversion). The app names are
# telemetrick's AC names, which is what the pages and maths channels look for.
MAPPED = {
    "Speed": ("Ground Speed", "kph", lambda v: v * 3.6),
    "Throttle": ("Throttle Pos", "%", lambda v: v * 100),
    "Brake": ("Brake Pos", "%", lambda v: v * 100),
    "Clutch": ("Clutch Pos", "%", lambda v: v * 100),
    "SteeringWheelAngle": ("Steering Angle", "deg", np.degrees),
    "Gear": ("Gear", "", lambda v: v),
    "RPM": ("Engine RPM", "rpm", lambda v: v),
    "Lap": ("Lap Number", "", lambda v: v),
    "LapDist": ("Lap Distance", "m", lambda v: v),
    "LapDistPct": ("Car Pos Norm", "", lambda v: v),
    # iRacing's lateral G has the same sign as its yaw rate; AC's has the opposite
    # sign (see maths.py), so flip it to keep every lateral-G calculation the same.
    "LatAccel": ("CG Accel Lateral", "G", lambda v: -v / G),
    "LongAccel": ("CG Accel Longitudinal", "G", lambda v: v / G),
    "VertAccel": ("CG Accel Vertical", "G", lambda v: v / G - 1),  # iRacing's includes gravity (1 G at rest); AC's doesn't
    "YawRate": ("Chassis Yaw Rate", "deg/s", np.degrees),
    "PitchRate": ("Chassis Pitch Rate", "deg/s", np.degrees),
    "RollRate": ("Chassis Roll Rate", "deg/s", np.degrees),
    "Yaw": ("Car Heading", "deg", np.degrees),
    "Pitch": ("Chassis Pitch Angle", "deg", np.degrees),
    "Roll": ("Chassis Roll Angle", "deg", np.degrees),
    "VelocityX": ("Chassis Velocity X", "m/s", lambda v: v),
    "VelocityY": ("Chassis Velocity Y", "m/s", lambda v: v),
    "VelocityZ": ("Chassis Velocity Z", "m/s", lambda v: v),
    "FuelLevel": ("Fuel Level", "l", lambda v: v),
    "AirDensity": ("Air Density", "kg/m3", lambda v: v),
    "AirTemp": ("Air Temp", "C", lambda v: v),
    "TrackTempCrew": ("Road Temp", "C", lambda v: v),
    "WindVel": ("Wind Speed", "kph", lambda v: v * 3.6),
    "WaterTemp": ("Water Temperature", "C", lambda v: v),
    "OilTemp": ("Oil Temperature", "C", lambda v: v),
    "BrakeABSactive": ("ABS Active", "", lambda v: v),
}
# Corners: iRacing says LF/RF/LR/RR, AC says FL/FR/RL/RR.
CORNERS = {"LF": "FL", "RF": "FR", "LR": "RL", "RR": "RR"}
for _ir, _ac in CORNERS.items():
    # Tyre surface temps are given across the tyre from the car's left to its right,
    # so on a left-hand tyre "left" is the outside edge, on a right-hand tyre the inside.
    _left, _right = ("Outer", "Inner") if _ir[0] == "L" else ("Inner", "Outer")
    MAPPED |= {
        f"{_ir}tempL": (f"Tire Temp {_left} {_ac}", "C", lambda v: v),
        f"{_ir}tempM": (f"Tire Temp Middle {_ac}", "C", lambda v: v),
        f"{_ir}tempR": (f"Tire Temp {_right} {_ac}", "C", lambda v: v),
        f"{_ir}pressure": (f"Tire Pressure {_ac}", "psi", lambda v: v * KPA_TO_PSI),
        f"{_ir}rideHeight": (f"Ride Height {_ac}", "mm", lambda v: v * 1000),
        f"{_ir}shockDefl": (f"Suspension Travel {_ac}", "mm", lambda v: v * 1000),
        f"{_ir}shockVel": (f"Damper Velocity {_ac}", "mm/s", lambda v: v * 1000),
        f"{_ir}speed": (f"Wheel Speed {_ac}", "kph", lambda v: v * 3.6),
        f"{_ir}brakeLinePress": (f"Brake Pressure {_ac}", "bar", lambda v: v),
    }

# Not worth keeping: PC performance, network, force feedback settings, pit-menu
# requests and the rates of change of lap deltas. Lat/Lon become Car Coord X/Y.
_SKIP_PREFIXES = ("Session", "Chan", "Cpu", "Gpu", "Mem", "FrameRate", "SteeringWheelPct",
                  "SteeringWheelLimiter", "SteeringWheelMaxForce", "SteeringWheelUseLinear",
                  "ShiftIndicatorPct", "ShiftPowerPct", "ShiftGrindRPM", "PitSv", "dp")
_SKIP_SUFFIXES = ("_DD", "_OK")
_SKIP = {"Lat", "Lon"}

# PlayerTrackSurface values (irsdk_TrkLoc)
NOT_IN_WORLD, OFF_TRACK = -1, 0


# --- Session info (YAML) --------------------------------------------------


def parse_session_info(text: str) -> dict:
    """iRacing's session info is YAML, but only a simple part of YAML: nested
    "key: value" maps and lists of maps ("- key: value"), indented with spaces.
    Reading that by hand is short, and avoids a YAML package (iRacing's text
    isn't always valid YAML anyway, e.g. team names with ':' in them).
    Every value comes back as text."""
    root: dict = {}
    stack: list[tuple[int, dict | list]] = [(-1, root)]  # open maps/lists and the indent of their lines
    pending = None  # (indent, map, key) of a "key:" whose value is on the lines below
    for raw in text.split("\n"):  # not splitlines(): that also splits at odd characters that some names contain
        raw = raw.rstrip("\r")
        body = raw.strip()
        if not body or body in ("---", "..."):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        dash = body.startswith("- ")
        if pending:  # now we can tell what the "key:" above holds
            p_indent, parent, key = pending
            pending = None
            if dash and indent >= p_indent:
                parent[key] = []
                stack.append((indent, parent[key]))
            elif not dash and indent > p_indent:
                parent[key] = {}
                stack.append((indent, parent[key]))
            else:
                parent[key] = ""
        if dash:  # a new item in the list at this indent
            while len(stack) > 1 and (stack[-1][0] > indent or not isinstance(stack[-1][1], list)):
                stack.pop()
            if not isinstance(stack[-1][1], list):
                continue  # not valid; skip the line
            item: dict = {}
            stack[-1][1].append(item)
            body, indent = body[2:].lstrip(), indent + 2
            stack.append((indent, item))
        else:  # a key in the map at this indent
            while len(stack) > 1 and (stack[-1][0] > indent or isinstance(stack[-1][1], list)):
                stack.pop()
        key, sep, value = body.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if value:
            stack[-1][1][key] = value.strip('"')
        else:
            pending = (indent, stack[-1][1], key)
    if pending:
        pending[1][pending[2]] = ""
    return root


def _decode(raw: bytes) -> str:
    """The session info mixes text encodings, even within one file: driver names
    come as UTF-8, but some track names in the Windows one ("Autódromo" as byte
    F3). So decode line by line: UTF-8 where it's valid, Windows otherwise."""
    lines = []
    for line in raw.split(b"\n"):
        try:
            lines.append(line.decode("utf-8"))
        except UnicodeDecodeError:
            lines.append(line.decode("cp1252", errors="replace"))
    return "\n".join(lines)


def _flatten(tree: dict, prefix: str) -> dict[str, str]:
    """{"Chassis": {"Front": {"ArbSetting": "2"}}} -> {"<prefix>_Chassis_Front_ArbSetting": "2"}"""
    out = {}
    for key, value in tree.items():
        if isinstance(value, dict):
            out |= _flatten(value, f"{prefix}_{key}")
        elif isinstance(value, str):
            out[f"{prefix}_{key}"] = value
    return out


def _number(text: str | None) -> float | None:
    """'3.65 km' -> 3.65"""
    try:
        return float(str(text).split()[0])
    except (ValueError, IndexError):
        return None


# --- Building the session -------------------------------------------------


def _even_clock(session_time: np.ndarray, freq: int) -> slice | np.ndarray:
    """Which sample to use at each tick of a perfectly even clock.

    The app assumes sample i is at i / freq. iRacing very occasionally misses a
    tick (about 1 in 30 000), so where it did, repeat the sample before the gap.
    If the clock ever goes backwards, the samples are used as they are."""
    if len(session_time) < 2:
        return slice(None)
    ticks = np.round((session_time - session_time[0]) * freq).astype(np.int64)
    if np.any(np.diff(ticks) <= 0) or ticks[-1] == len(ticks) - 1:
        return slice(None)
    return np.searchsorted(ticks, np.arange(ticks[-1] + 1), side="right") - 1


def _beacons(lap: np.ndarray, pct: np.ndarray, pit: np.ndarray, freq: int) -> list[float]:
    """Times (s) the car crossed the start/finish line on track.

    A crossing is where iRacing's lap counter goes up and the lap % wraps from
    ~1 back to ~0. Crossings in the pit lane are left out, so (as in AC) the out
    lap runs from the pit box to the first crossing on track. The line is
    crossed somewhere between two samples; the lap % says where, which makes
    lap times accurate to about a millisecond instead of a 60th of a second."""
    i = np.flatnonzero((np.diff(lap) > 0) & (pct[:-1] > 0.9) & (pct[1:] < 0.1) & (pit[:-1] == 0) & (pit[1:] == 0)) + 1
    before, after = 1.0 - pct[i - 1], pct[i]
    fraction = before / np.maximum(before + after, 1e-9)
    return [float(x) for x in (i - 1 + fraction) / freq]


def _invalid_flag(n: int, freq: int, beacons: list[float], pit: np.ndarray, surface: np.ndarray | None,
                  incidents: np.ndarray | None) -> np.ndarray:
    """1 from the moment a lap goes wrong until the next line crossing: off track
    (iRacing's own track limits), an incident, a tow/reset, or the pit lane."""
    bad = pit > 0
    if surface is not None:
        bad |= (surface == OFF_TRACK) | (surface == NOT_IN_WORLD)
    if incidents is not None:
        bad[1:] |= np.diff(incidents) > 0
    lap_of = np.searchsorted(np.asarray(beacons) * freq, np.arange(n), side="right")
    flag = np.zeros(n)
    for k in np.unique(lap_of):
        m = lap_of == k
        flag[m] = np.maximum.accumulate(bad[m])
    return flag


def _positions(lat: np.ndarray, lon: np.ndarray, origin: tuple[float, float] | None) -> tuple[np.ndarray, np.ndarray] | None:
    """GPS degrees -> metres east (x) and north (y) of the track's reference point.

    Using the track's own reference point (from the session info) means every
    session at a track shares the same x/y, so laps from different files line
    up on one map. Over a few km the earth is flat enough for this simple
    projection. Before the car is on track iRacing logs 0, 0: those samples
    take the nearest real position instead."""
    ok = (lat != 0) | (lon != 0)
    if not ok.any():
        return None
    idx = np.arange(len(lat))
    lat, lon = np.interp(idx, idx[ok], lat[ok]), np.interp(idx, idx[ok], lon[ok])
    lat0, lon0 = origin or (float(np.median(lat)), float(np.median(lon)))
    x = EARTH_RADIUS_M * np.cos(np.radians(lat0)) * np.radians(lon - lon0)
    y = EARTH_RADIUS_M * np.radians(lat - lat0)
    return x, y


def _tidy(values: np.ndarray, unit: str) -> tuple[np.ndarray, str]:
    """Friendlier units for the channels kept under their iRacing names."""
    if unit == "%":  # iRacing's "%" channels actually run 0..1
        return values * 100, "%"
    if unit == "rad":
        return np.degrees(values), "deg"
    if unit == "rad/s":
        return np.degrees(values), "deg/s"
    if unit.startswith("irsdk_"):  # a state or a set of flags, not a measurement
        return values, ""
    return values, unit


def who_where(info: dict) -> tuple[str, str, str]:
    """(driver, car, track) from the session info. The session lists every car in
    the session; ours is the one at DriverCarIdx."""
    weekend, drivers = info.get("WeekendInfo", {}), info.get("DriverInfo", {})
    me = next((d for d in drivers.get("Drivers", []) if d.get("CarIdx") == drivers.get("DriverCarIdx")), {})
    track = weekend.get("TrackDisplayName") or weekend.get("TrackName", "")
    if weekend.get("TrackConfigName"):
        track += f" - {weekend['TrackConfigName']}"
    return me.get("UserName", ""), me.get("CarScreenName") or me.get("CarPath", ""), track


def read_session_info(path: str | Path) -> tuple[dict, datetime | None]:
    """Just the session info and start time, without the samples: quick, for
    looking through many files (e.g. for their setups)."""
    with open(path, "rb") as f:
        head = f.read(_DISK.size + 112)
        info_len, info_pos = _HEADER.unpack_from(head, 0)[4:6]
        start_unix = _DISK.unpack_from(head, 112)[0]
        f.seek(info_pos)
        raw = f.read(info_len)
    info = parse_session_info(_decode(raw.split(b"\0", 1)[0]))
    return info, datetime.fromtimestamp(start_unix) if start_unix > 0 else None


# Setup fields that are readings from the last run rather than settings
# (they'd make every run's setup look different)
_SETUP_READINGS = ("LastHotPressure", "LastTemps", "TreadRemaining", "UpdateCount")


def setup_values(info: dict) -> dict[str, str]:
    """The car setup, as in iRacing's garage: {"Chassis_LeftFront_Camber": "-4.4 deg", ...}.
    Real units, unlike AC's setup-screen clicks."""
    values = _flatten(info.get("CarSetup", {}), "")
    return {k.removeprefix("_"): v for k, v in values.items() if not any(r in k for r in _SETUP_READINGS)}


def read_ibt(path: str | Path) -> LdSession:
    path = Path(path)
    buf = path.read_bytes()
    (_ver, _status, freq, _update, info_len, info_pos, n_vars, vars_pos, _n_buf, sample_len) = _HEADER.unpack_from(buf, 0)
    (data_pos,) = _DATA_POS.unpack_from(buf, 48)
    start_unix, _t0, _t1, _laps, n_samples = _DISK.unpack_from(buf, 112)
    # A file cut short (iRacing crashed) holds fewer samples than it says
    n_samples = min(n_samples, (len(buf) - data_pos) // sample_len) if sample_len > 0 else 0
    if n_samples < 2 or freq <= 0:
        raise ValueError(f"{path.name}: no samples")

    # The table: one column per channel, one row per sample
    names, formats, offsets, units, descriptions = [], [], [], {}, {}
    for i in range(n_vars):
        kind, offset, count, _, name, desc, unit = _VAR.unpack_from(buf, vars_pos + i * _VAR.size)
        if kind in _TYPES and count == 1:  # skips text and the odd array (e.g. 360 Hz steering torque)
            name = _text(name)
            names.append(name)
            formats.append(_TYPES[kind])
            offsets.append(offset)
            units[name], descriptions[name] = _text(unit), _text(desc)
    table = np.frombuffer(buf, np.dtype(dict(names=names, formats=formats, offsets=offsets, itemsize=sample_len)),
                          count=n_samples, offset=data_pos)
    use = _even_clock(table["SessionTime"], freq) if "SessionTime" in units else slice(None)
    col = lambda name: table[name][use].astype(np.float64)  # noqa: E731
    n = n_samples if isinstance(use, slice) else len(use)

    info = parse_session_info(_decode(buf[info_pos:info_pos + info_len].split(b"\0", 1)[0]))
    weekend, drivers = info.get("WeekendInfo", {}), info.get("DriverInfo", {})
    me = next((d for d in drivers.get("Drivers", []) if d.get("CarIdx") == drivers.get("DriverCarIdx")), {})

    channels: dict[str, Channel] = {}

    def add(name: str, unit: str, values: np.ndarray, description: str) -> None:
        # float32 halves the memory; still far more precise than the sim's own numbers
        channels[name] = Channel(name, unit, freq, np.asarray(values, dtype=np.float32), description=description)

    for ir_name, (name, unit, convert) in MAPPED.items():
        if ir_name in units:
            add(name, unit, convert(col(ir_name)), f"{descriptions[ir_name]} (iRacing {ir_name})")
    if all(f"Ride Height {c}" in channels for c in CORNERS.values()):
        for axle, (a, b) in (("Front", ("FL", "FR")), ("Rear", ("RL", "RR"))):
            add(f"Ride Height {axle}", "mm", (channels[f"Ride Height {a}"].data + channels[f"Ride Height {b}"].data) / 2,
                f"Average of the two {axle.lower()} ride heights")

    if "Lat" in units and "Lon" in units:
        origin = (_number(weekend.get("TrackLatitude")), _number(weekend.get("TrackLongitude")))
        xy = _positions(col("Lat"), col("Lon"), origin if None not in origin else None)
        if xy is not None:
            add("Car Coord X", "m", xy[0], "Metres east of the track's reference point (from iRacing's GPS Lon)")
            add("Car Coord Y", "m", xy[1], "Metres north of the track's reference point (from iRacing's GPS Lat)")

    pit = col("OnPitRoad") if "OnPitRoad" in units else np.zeros(n)
    beacons = _beacons(col("Lap"), col("LapDistPct"), pit, freq) if {"Lap", "LapDistPct"} <= units.keys() else []
    add("Lap Invalidated", "", _invalid_flag(
        n, freq, beacons, pit,
        col("PlayerTrackSurface") if "PlayerTrackSurface" in units else None,
        col("PlayerCarMyIncidentCount") if "PlayerCarMyIncidentCount" in units else None,
    ), "1 once the lap has gone off track, had an incident or been in the pit lane (worked out; iRacing doesn't log this)")

    for name in names:
        if name in MAPPED or name in _SKIP or name.startswith(_SKIP_PREFIXES) or name.endswith(_SKIP_SUFFIXES):
            continue
        values = col(name)
        if np.all(values == values[0]):
            continue  # never changes during the run: nothing to plot
        values, unit = _tidy(values, units[name])
        add(name, unit, values, f"{descriptions[name]} (iRacing)")

    # Official sector lines, as a share of the lap -> metres. The longest lap distance
    # is the precise track length, but only if a lap was finished; otherwise use
    # the session info's (rounded to 10 m).
    lap_length = float(channels["Lap Distance"].data.max()) if "Lap Distance" in channels else 0.0
    if not beacons:
        lap_length = 1000 * (_number(weekend.get("TrackLength")) or 0.0) or lap_length
    sectors = info.get("SplitTimeInfo", {}).get("Sectors", [])
    sector_lines = [round(p * lap_length, 1) for s in sectors if (p := _number(s.get("SectorStartPct")) or 0) > 0]

    driver, car, track = who_where(info)
    started = datetime.fromtimestamp(start_unix) if start_unix > 0 else None
    details = {
        "Venue": weekend.get("TrackName", ""),
        "Venue Length": str(round(lap_length, 1)),
        "Event": weekend.get("EventType", ""),
        "Car Path": me.get("CarPath", ""),
        "Setup Name": drivers.get("DriverSetupName", ""),
        "Fuel Tank Capacity": drivers.get("DriverCarFuelMaxLtr", ""),
        **_flatten(info.get("CarSetup", {}), "CarSetup"),
    }
    return LdSession(
        path=path,
        driver=driver,
        car=car,
        track=track,
        date=started.strftime("%d/%m/%Y") if started else "",
        time=started.strftime("%H:%M:%S") if started else "",
        channels=channels,
        beacons=beacons,
        details=details,
        source="iracing",
        sector_lines_m=sector_lines,
    )
