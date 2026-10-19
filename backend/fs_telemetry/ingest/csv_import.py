"""Read telemetry from CSV files (other sims, data loggers, simulation output,
or the app's own CSV downloads).

CSV has no fixed layout, so this works out the layout of each file first:
  * the delimiter (comma, semicolon, tab or |) and whether numbers use a
    decimal comma ("12,5"), as Excel does in many European languages
  * the header row (column names), an optional units row under it, and any
    "key, value" lines above it (MoTeC i2's CSV export puts the venue, driver
    and lap beacons there)
  * what the rows are spaced by: a time column, or a distance column (time is
    then worked out from distance and speed). If it can't tell, it says so.

Then, like the iRacing reader, it makes the run look like an AC one to the rest
of the app, so every page, layout and maths channel works unchanged:
  * columns get telemetrick's AC names and units (e.g. "Speed" m/s ->
    "Ground Speed" kph), guessed from common column names
  * the guess is saved per source (see MappingStore) in the app's data folder,
    so it can be corrected by hand once and is remembered for the next file
  * every channel goes onto one even clock: the app assumes sample i is at
    i / freq, but CSV rows can be unevenly spaced and slower channels often
    leave cells blank between their samples
  * GPS latitude/longitude become x/y metres for the track map
  * laps come from (best first): MoTeC beacon markers, a lap number column,
    a lap timer going back to 0, a lap distance going back to 0, the car coming
    back past where it started (GPS); otherwise the file is one run
  * "Lap Distance" is built for lining laps up (from distance, speed or GPS)
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import config
from .ibt import MAPPED, _positions
from .motec_ld import Channel, LdSession

log = logging.getLogger(__name__)

G = 9.81
DELIMITERS = [",", ";", "\t", "|"]  # tried in this order; the first that fits the file wins a tie

# Channels with a special job. They're names you can give a column in the mapping file.
TIME, DISTANCE, LAP = "Time", "Distance", "Lap Number"
LAT, LON = "GPS Latitude", "GPS Longitude"
LAP_TIME = "Lap Time"

# The units the app uses for each channel it knows (the AC names and units the iRacing reader uses too).
APP_UNITS: dict[str, str] = {name: unit for name, unit, _ in MAPPED.values()} | {
    TIME: "s", DISTANCE: "m", "Lap Distance": "m", LAP_TIME: "s", LAP: "",
    LAT: "deg", LON: "deg", "Car Coord X": "m", "Car Coord Y": "m",
}

# Common column names -> our channel. Compared without case, spaces or punctuation
# ("Throttle_Pos", "throttle pos" and "ThrottlePos" all match "throttlepos").
# The app's own channel names always match too (e.g. a file from the app's CSV download).
ALIASES: dict[str, list[str]] = {
    TIME: ["time", "sessiontime", "elapsedtime", "elapsed", "timestamp", "t", "timesec", "times", "clock"],
    DISTANCE: ["distance", "dist", "lapdistance", "lapdist", "odometer", "distancetravelled", "distancetraveled"],
    LAP: ["lap", "lapnumber", "lapno", "lapnum", "lapcount", "lapcounter", "currentlap", "laps", "lapindex"],
    LAP_TIME: ["laptime", "currentlaptime", "laptimecurrent", "lapclock", "laptimer"],
    LAT: ["latitude", "lat", "gpslatitude", "gpslat", "poslat"],
    LON: ["longitude", "lon", "long", "lng", "gpslongitude", "gpslon", "gpslong", "poslon"],
    "Car Coord X": ["x", "posx", "positionx", "worldx", "carx"],
    "Car Coord Y": ["y", "posy", "positiony", "worldy", "cary"],
    "Ground Speed": ["speed", "groundspeed", "vehiclespeed", "carspeed", "velocity", "gpsspeed", "speedoverground"],
    "Throttle Pos": ["throttle", "throttlepos", "throttleposition", "throttlepedal", "gas", "accelerator",
                     "acceleratorpedal", "tps", "pedalthrottle"],
    "Brake Pos": ["brake", "brakepos", "brakeposition", "brakepedal", "pedalbrake"],
    "Clutch Pos": ["clutch", "clutchpos", "clutchposition", "clutchpedal"],
    "Steering Angle": ["steering", "steer", "steeringangle", "steerangle", "steeringwheelangle", "swa", "steeringwheel"],
    "Gear": ["gear", "currentgear", "gearposition"],
    "Engine RPM": ["rpm", "enginerpm", "enginespeed", "revs"],
    "CG Accel Lateral": ["latg", "lateralg", "glat", "glateral", "accellat", "lateralacceleration", "latacc", "lataccel"],
    "CG Accel Longitudinal": ["longg", "longitudinalg", "glong", "glongitudinal", "accellong",
                              "longitudinalacceleration", "longacc", "longaccel"],
    "Chassis Yaw Rate": ["yawrate", "yawvelocity"],
    "Fuel Level": ["fuel", "fuellevel", "fuelremaining"],
    "Water Temperature": ["watertemp", "coolanttemp", "watertemperature"],
    "Oil Temperature": ["oiltemp", "oiltemperature"],
}

# Ways of writing a unit -> one spelling
UNIT_SPELLINGS = {
    "km/h": "kph", "kmh": "kph", "kph": "kph", "kmph": "kph", "mph": "mph", "m/s": "m/s", "mps": "m/s", "ms-1": "m/s",
    "m.s-1": "m/s", "%": "%", "pct": "%", "percent": "%", "deg": "deg", "degree": "deg", "degrees": "deg", "°": "deg",
    "rad": "rad", "radians": "rad", "deg/s": "deg/s", "°/s": "deg/s", "rad/s": "rad/s", "g": "G",
    "m/s2": "m/s2", "m/s^2": "m/s2", "m/s²": "m/s2", "ms-2": "m/s2", "s": "s", "sec": "s", "secs": "s",
    "seconds": "s", "ms": "ms", "msec": "ms", "min": "min", "m": "m", "metres": "m", "meters": "m", "km": "km",
    "mi": "mi", "miles": "mi", "ft": "ft", "c": "C", "°c": "C", "degc": "C", "f": "F", "°f": "F", "kpa": "kPa",
    "psi": "psi", "bar": "bar", "rpm": "rpm", "l": "l", "litres": "l", "liters": "l",
}

# (file unit, app unit) -> conversion
CONVERSIONS = {
    ("m/s", "kph"): lambda v: v * 3.6,
    ("mph", "kph"): lambda v: v * 1.609344,
    ("rad", "deg"): np.degrees,
    ("rad/s", "deg/s"): np.degrees,
    ("m/s2", "G"): lambda v: v / G,
    ("km", "m"): lambda v: v * 1000,
    ("mi", "m"): lambda v: v * 1609.344,
    ("ft", "m"): lambda v: v * 0.3048,
    ("ms", "s"): lambda v: v / 1000,
    ("min", "s"): lambda v: v * 60,
    ("kPa", "psi"): lambda v: v * 0.1450377,
    ("bar", "psi"): lambda v: v * 14.50377,
    ("F", "C"): lambda v: (v - 32) / 1.8,
}

# Laps from GPS: the car is "back where it started" within this many metres,
# having been at least FAR_M away in between
NEAR_M, FAR_M = 15.0, 100.0
GPS_END_S = 2.0  # ...and not in the last moments of the file
KEEP_START_M = 50.0  # a one-run file's distance starting below this is taken as distance from the line
MAX_SAMPLES = 20_000_000  # per channel, so a broken time column can't fill the memory

MAPPINGS_FILE = config.DATA_DIR / "csv_mappings.json"
MAPPINGS_HELP = (
    "How the app reads CSV telemetry. One entry per source (a set of column names, i.e. one logger or export setup). "
    "For each column: 'channel' is the app's name for it (e.g. Ground Speed, Throttle Pos, Brake Pos, Steering Angle, "
    "Time, Distance, Lap Number, GPS Latitude, GPS Longitude; empty = ignore the column) and 'unit' is the unit the "
    "numbers in the file are in (e.g. m/s, km/h, mph, rad, %). Fill in driver, car and track if the files don't say. "
    "Edit with Notepad, save, and refresh the app."
)


class CsvError(ValueError):
    """A CSV the reader can't make sense of. The message says why, in plain words."""


def _key(name: str) -> str:
    """'Throttle_Pos (%)' -> 'throttlepos' - for comparing column names loosely."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


GUESSES = {_key(name): name for name in APP_UNITS} | {alias: ch for ch, aliases in ALIASES.items() for alias in aliases}


def _unit(text: str, app_unit: str | None = None) -> str:
    """One spelling for a unit. "ms" after a speed means metres per second, not milliseconds."""
    u = text.strip().lower().replace(" ", "")
    if u == "ms" and app_unit not in (None, "s"):
        return "m/s"
    return UNIT_SPELLINGS.get(u, text.strip())


# --- Reading the file's layout ----------------------------------------------


@dataclass
class Layout:
    """What the format detection found."""

    delimiter: str
    decimal_comma: bool
    names: list[str]  # column names, units taken off
    units: list[str]  # each column's unit as written in the file ("" if none)
    rows: list[list[str]]  # the data, as text
    meta: dict[str, str] = field(default_factory=dict)  # "key, value" lines above the header


_NUMBER = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")
_COMMA_DECIMAL = re.compile(r"^[+-]?\d*,\d+([eE][+-]?\d+)?$")
_DOT_DECIMAL = re.compile(r"^[+-]?\d*\.\d+([eE][+-]?\d+)?$")
_CLOCK = re.compile(r"^\d+(:\d+){1,2}([.,]\d+)?$")  # 1:23.456 or 0:01:23.456
_NAME_AND_UNIT = re.compile(r"^(.*?)\s*[\[(]([^\[\]()]*)[\])]\s*$")  # "Speed [km/h]" or "Speed (km/h)"


def _is_number(text: str) -> bool:
    t = text.strip()
    return bool(_NUMBER.match(t) or _CLOCK.match(t))


def _numeric_share(row: list[str]) -> float:
    filled = [x for x in row if x.strip()]
    return sum(_is_number(x) for x in filled) / len(filled) if filled else 0.0


def _is_blank(row: list[str]) -> bool:
    return not any(x.strip() for x in row)


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _find_header(rows: list[list[str]], width: int) -> tuple[int, int | None, int] | None:
    """(header row, units row or None, first data row) in the first rows of a file
    whose data rows have `width` fields, or None if it doesn't fit."""
    def fits(r):  # trailing empty fields (a line ending in a delimiter) don't count
        return len(r) == width or (len(r) > width and _is_blank(r[width:]))

    def is_data(r):
        return fits(r) and not _is_blank(r) and _numeric_share(r) >= 0.6

    for i, r in enumerate(rows):
        following = [x for x in rows[i:i + 4] if not _is_blank(x)]
        if is_data(r) and all(is_data(x) for x in following):
            break
    else:
        return None
    # Up to two text rows just above the data (blank lines between are fine): header, then units
    above = []
    for j in range(i - 1, -1, -1):
        if _is_blank(rows[j]):
            continue
        if not fits(rows[j]) or _numeric_share(rows[j]) >= 0.6 or len(above) == 2:
            break
        above.append(j)
    if not above:
        return None
    if len(above) == 2:
        header, units = above[1], above[0]
        if all(len(x.strip()) <= 12 for x in rows[units]) and rows[units] != rows[header]:
            return header, units, i
    return above[0], None, i


def detect_layout(text: str) -> Layout:
    lines = text.splitlines()
    filled = [ln for ln in lines if ln.strip()]
    if len(filled) < 3:
        raise CsvError("The file is too short: it needs a header row and at least two rows of data")
    tail, head = filled[-200:], lines[:300]
    best = None
    for d in DELIMITERS:
        counts = Counter(len(r) for r in csv.reader(tail, delimiter=d))
        width, n = counts.most_common(1)[0]
        if width < 2 or n < 0.9 * len(tail):
            continue  # data rows don't split evenly with this delimiter
        found = _find_header(list(csv.reader(head, delimiter=d)), width)
        if found and (best is None or width > best[1]):
            best = (d, width, found)
    if best is None:
        raise CsvError("Can't find the columns: there should be a header row naming them, then rows of numbers "
                       "split by commas, semicolons or tabs")
    delimiter, width, (h, u, first) = best
    rows = list(csv.reader(lines, delimiter=delimiter))
    data = [r for r in rows[first:] if not _is_blank(r)]

    # Decimal comma: "12,5" in the data and (almost) no "12.5"
    sample = [x.strip() for r in data[:500] for x in r[:width]]
    commas = sum(bool(_COMMA_DECIMAL.match(x)) for x in sample)
    dots = sum(bool(_DOT_DECIMAL.match(x)) for x in sample)
    decimal_comma = commas > dots

    names, units = [], []
    unit_row = rows[u] if u is not None else []
    for j in range(width):
        raw = rows[h][j].strip() if j < len(rows[h]) else ""
        m = _NAME_AND_UNIT.match(raw)
        name, unit = (m.group(1).strip(), m.group(2).strip()) if m and m.group(1).strip() else (raw, "")
        if j < len(unit_row) and unit_row[j].strip():
            unit = unit_row[j].strip()
        name = name or f"Column {j + 1}"
        while name in names:  # two columns with the same name: keep both
            name += " 2"
        names.append(name)
        units.append(unit)

    meta = {}
    for r in rows[:h]:
        cells = [x.strip() for x in r]
        if len(cells) >= 2 and cells[0] and cells[1]:
            meta[cells[0]] = cells[1]
    return Layout(delimiter, decimal_comma, names, units, data, meta)


def _clock_seconds(text: str) -> float:
    """'1:23.456' -> 83.456, '0:01:23.4' -> 83.4"""
    total = 0.0
    for part in text.replace(",", ".").split(":"):
        total = total * 60 + float(part)
    return total


def _to_numbers(cells: list[str], decimal_comma: bool) -> np.ndarray:
    """A column of text -> numbers; blank or unreadable cells become NaN (no sample)."""
    a = np.char.strip(np.array(cells, dtype=str))
    if decimal_comma:
        a = np.char.replace(a, ",", ".")
    a[a == ""] = "nan"
    try:
        return a.astype(np.float64)  # quick: all numbers (numpy reads the text itself)
    except ValueError:
        pass

    def one(x: str) -> float:
        try:
            return float(x)
        except ValueError:
            pass
        if _CLOCK.match(x):
            return _clock_seconds(x)
        return {"true": 1.0, "false": 0.0, "yes": 1.0, "no": 0.0, "on": 1.0, "off": 0.0}.get(x.lower(), np.nan)

    return np.array([one(x) for x in a], dtype=np.float64)


# --- Column mapping -------------------------------------------------------------


def source_signature(names: list[str], units: list[str] | None = None) -> str:
    """Files with the same columns and declared units share a mapping.
    Different speed/time units must never reuse an earlier conversion. Order does not matter."""
    columns = [n.strip().lower() for n in names]
    units = units if units is not None else [""] * len(names)
    columns = [f"{name} [{_unit(unit)}]" for name, unit in zip(columns, units)]
    text = "\n".join(sorted(columns))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def guess_channel(name: str, unit: str) -> tuple[str, str]:
    """(our channel, file's unit) for a column. Columns we don't recognise keep their own name.
    A unit written into the name ("speed_kph", "Time_ms") counts if it suits the channel."""
    key = _key(name)
    if key in GUESSES:
        return GUESSES[key], unit
    for token in sorted(UNIT_SPELLINGS, key=len, reverse=True):
        t = _key(token)
        if t and key.endswith(t) and key[: -len(t)] in GUESSES:
            channel = GUESSES[key[: -len(t)]]
            app_unit = APP_UNITS.get(channel, "")
            u = _unit(token, app_unit)
            if u == app_unit or (u, app_unit) in CONVERSIONS:
                return channel, unit or token
    return name, unit


def guess_mapping(names: list[str], units: list[str]) -> dict:
    columns, taken = {}, set()
    for name, unit in zip(names, units):
        channel, unit = guess_channel(name, unit)
        if channel in taken:  # two columns guessed as the same channel: the first one gets it
            channel = name
        taken.add(channel)
        columns[name] = {"channel": channel, "unit": unit}
    return {"driver": "", "car": "", "track": "", "columns": columns}


_lock = threading.Lock()  # the server reads files on several threads at once


class MappingStore:
    """Saved column mappings, one per source, in a JSON file in the app's data folder
    (FS Telemetry\\csv_mappings.json). Written by the app the first time it sees a
    source; edit it by hand to fix a guess (the file says how)."""

    def __init__(self, path: Path | None = None):
        self.path = path or MAPPINGS_FILE

    def _read(self) -> dict | None:
        """The whole file, {} if there isn't one, None if it can't be read (a typo while editing)."""
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (ValueError, OSError) as e:
            log.warning("Can't read %s (%s): using guessed column mappings until it's fixed", self.path, e)
            return None

    def get(self, signature: str) -> dict | None:
        entry = (self._read() or {}).get("sources", {}).get(signature)
        return entry if isinstance(entry, dict) and isinstance(entry.get("columns"), dict) else None

    def save(self, signature: str, entry: dict) -> None:
        with _lock:
            data = self._read()
            if data is None:
                return  # never overwrite a file someone is part-way through fixing
            data["how_to_edit"] = MAPPINGS_HELP
            data.setdefault("sources", {})[signature] = entry
            # Write to a temporary file then swap it in, so a crash can't leave half a file
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)


def mapping_for(names: list[str], units: list[str], store: MappingStore, example_file: str) -> tuple[dict, str]:
    """The saved mapping for this source, or a guessed one (saved, so it's remembered)."""
    signature = source_signature(names, units)
    entry = store.get(signature)
    if entry is None:
        entry = guess_mapping(names, units) | {"example_file": example_file}
        store.save(signature, entry)
    return entry, signature


def to_app_units(values: np.ndarray, file_unit: str, channel: str) -> tuple[np.ndarray, str]:
    """Convert a column to the unit the app uses for that channel (e.g. m/s -> kph)."""
    app_unit = APP_UNITS.get(channel)
    if app_unit is None:  # not a channel the app knows: keep it as it is
        return values, file_unit
    u = _unit(file_unit, app_unit)
    finite = values[np.isfinite(values)]
    if app_unit == "%" and u in ("", "%") and len(finite) and finite.min() >= 0 and 0 < finite.max() <= 1.0:
        return values * 100, "%"  # pedals logged 0..1
    if u in ("", app_unit):
        return values, app_unit
    if (u, app_unit) in CONVERSIONS:
        return CONVERSIONS[(u, app_unit)](values), app_unit
    return values, file_unit  # a unit we can't convert: say what it really is rather than pretend


# --- Time base ---------------------------------------------------------------


def _rising(t: np.ndarray) -> np.ndarray:
    """True for each sample later than every sample before it (stale or repeated
    rows are dropped, as in laps.py)."""
    before = np.maximum.accumulate(np.concatenate([[-np.inf], t[:-1]]))
    return np.isfinite(t) & (t > before)


def _clean_time(t: np.ndarray, name: str) -> tuple[np.ndarray, list[float]]:
    """Session time from a time column: lap timer resets are joined up (and give
    lap beacons), and rows that go backwards are dropped (NaN)."""
    t = t.copy()
    ok = np.flatnonzero(np.isfinite(t))
    if len(ok) < 2:
        raise CsvError(f"The time column '{name}' has no numbers in it")
    beacons = []
    v = t[ok]
    # A lap timer: drops back to ~0 from well over a second
    resets = np.flatnonzero((np.diff(v) < -1.0) & (v[1:] < 0.5 * v[:-1]))
    if len(resets):
        step = float(np.median(np.diff(v)[np.diff(v) > 0])) if np.any(np.diff(v) > 0) else 0.0
        offset = np.zeros(len(v))
        for r in resets:
            offset[r + 1:] += v[r] - v[r + 1] + step
        v = v + offset
        beacons = [float(offset[r + 1]) for r in resets]  # the session time when the timer read 0
    t[ok] = v
    keep = _rising(t)
    if keep.sum() < 0.8 * len(ok):
        raise CsvError(f"The time column '{name}' jumps backwards and forwards, so the rows can't be put in order")
    t[~keep] = np.nan
    return t, beacons


def _time_from_distance(d: np.ndarray, kph: np.ndarray) -> np.ndarray:
    """Session time for distance-spaced rows: time = distance / speed, row by row."""
    ok = np.isfinite(d) & np.isfinite(kph)
    t = np.full(len(d), np.nan)
    if ok.sum() < 2:
        return t
    dd = np.diff(d[ok])
    v = np.maximum((kph[ok][1:] + kph[ok][:-1]) / 2 / 3.6, 1.0)  # m/s; at least 1 so a stop doesn't divide by 0
    t[ok] = np.concatenate([[0.0], np.cumsum(np.maximum(dd, 0) / v)])
    return t


def _unwrap_distance(d: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """A lap distance going back to 0 at the line -> distance since the start of the
    file, plus the rows where it went back to 0."""
    d = d.copy()
    ok = np.flatnonzero(np.isfinite(d))
    if len(ok) < 2:
        return d, []
    v = d[ok]
    drops = np.flatnonzero(np.diff(v) < -0.5 * np.nanmax(v))
    if len(drops):
        lap_length = float(np.median(v[drops]))  # the distance just before each reset
        v = v + np.concatenate([[0], np.cumsum(np.isin(np.arange(len(v) - 1), drops))]) * lap_length
    d[ok] = v
    return d, [int(ok[i + 1]) for i in drops]


# --- Laps ----------------------------------------------------------------------


def _gps_returns(x: np.ndarray, y: np.ndarray, freq: int, kph: np.ndarray | None) -> list[float]:
    """Times the car came back past the place it was when it first got moving.
    With no lap information, that place is used as the start/finish line."""
    moving = np.flatnonzero(kph > 10) if kph is not None else np.arange(len(x))
    if not len(moving):
        return []
    start = moving[0]
    r = np.hypot(x - x[start], y - y[start])
    near = r < NEAR_M
    edges = np.flatnonzero(np.diff(near.astype(np.int8))) + 1  # where near starts or stops
    bounds = [0, *edges, len(r)]
    beacons, last = [], start
    for a, b in zip(bounds, bounds[1:]):
        if a <= start or not near[a]:
            continue
        if np.any(r[last:a] > FAR_M):  # went away and came back: a lap
            closest = a + int(np.argmin(r[a:b]))
            if closest > len(r) - GPS_END_S * freq:
                break  # back where it started as the file ends: one lap, e.g. a lap from the app's CSV download
            beacons.append(closest / freq)
            last = closest
    return beacons


def _lap_distance(run_d: np.ndarray, beacons: list[float], freq: int) -> np.ndarray:
    """Lap Distance from the distance covered since the start of the file: back to 0
    at each beacon. The out lap is counted back from the first beacon, so it ends at
    one lap length, like AC's (which starts part-way round, in the pit lane)."""
    t = np.arange(len(run_d)) / freq
    if not beacons:
        # One run. A file whose own distance starts at the line (e.g. a lap from the app's CSV
        # download, which starts a few metres in) keeps it; anything else starts at 0.
        return run_d if 0 <= run_d[0] < KEEP_START_M else run_d - run_d[0]
    at = np.interp(beacons, t, run_d)
    spans = np.diff(at)
    lap_length = float(np.median(spans)) if len(spans) else max(at[0] - run_d[0], run_d[-1] - at[0])
    lap_of = np.searchsorted(beacons, t, side="right")  # 0 = out lap
    out = np.empty_like(run_d)
    out[lap_of == 0] = lap_length - (at[0] - run_d[lap_of == 0])
    later = lap_of > 0
    out[later] = run_d[later] - at[lap_of[later] - 1]
    return out


# --- Meta lines (MoTeC i2 CSV export and similar) --------------------------------


def _meta(meta: dict[str, str], *keys: str) -> str:
    low = {k.lower(): v for k, v in meta.items()}
    return next((low[k] for k in keys if low.get(k)), "")


def _date_time(meta: dict[str, str], path: Path) -> tuple[str, str]:
    """('dd/mm/yyyy', 'hh:mm:ss') from the meta lines, else the file's modified time."""
    date, clock = _meta(meta, "log date", "date"), _meta(meta, "log time", "time", "start time")
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            d = datetime.strptime(date, fmt)
            break
        except ValueError:
            continue
    else:
        modified = datetime.fromtimestamp(path.stat().st_mtime)
        return modified.strftime("%d/%m/%Y"), modified.strftime("%H:%M:%S")
    for fmt in ("%H:%M:%S", "%I:%M:%S %p", "%H:%M"):
        try:
            return d.strftime("%d/%m/%Y"), datetime.strptime(clock, fmt).strftime("%H:%M:%S")
        except ValueError:
            continue
    return d.strftime("%d/%m/%Y"), "00:00:00"


# --- Building the session ---------------------------------------------------------


def _on_clock(t: np.ndarray, values: np.ndarray, n: int, freq: int, discrete: bool) -> np.ndarray | None:
    """A column's samples (at times t, NaN = no sample) -> one value per tick of the even clock.
    Gear, lap number and the like are held until the next sample instead of blended."""
    ok = np.isfinite(t) & np.isfinite(values)
    if not ok.any():
        return None
    ts, vs = t[ok], values[ok]
    grid = np.arange(n) / freq
    if discrete:
        return vs[np.clip(np.searchsorted(ts, grid, side="right") - 1, 0, len(vs) - 1)]
    return np.interp(grid, ts, vs)


def _is_discrete(values: np.ndarray, unit: str) -> bool:
    finite = values[np.isfinite(values)]
    return unit == "" and len(finite) > 0 and bool(np.all(np.mod(finite, 1) == 0))


def read_csv(path: str | Path, mappings: MappingStore | None = None) -> LdSession:
    path = Path(path)
    layout = detect_layout(_read_text(path))
    store = mappings or MappingStore()
    entry, signature = mapping_for(layout.names, layout.units, store, path.name)

    # Each column as numbers, in the app's units, under its channel name
    columns: dict[str, tuple[str, np.ndarray, str]] = {}  # channel -> (column, values, unit)
    for j, name in enumerate(layout.names):
        spec = entry["columns"].get(name)
        if not isinstance(spec, dict):  # a line missing from the saved mapping: guess it from the file
            channel, unit = guess_channel(name, layout.units[j])
            spec = {"channel": channel, "unit": unit}
        channel = str(spec.get("channel", name)).strip()
        if not channel:
            continue  # set to be ignored
        if channel in columns:  # a second column for a channel that's taken: keep it under its own name
            channel = name
            if channel in columns:
                continue
        values = _to_numbers([r[j] if j < len(r) else "" for r in layout.rows], layout.decimal_comma)
        if not np.isfinite(values).any():
            continue  # a text column (comments, names...)
        file_unit = str(spec.get("unit", layout.units[j]))
        values, unit = to_app_units(values, file_unit, channel)
        columns[channel] = (name, values, unit)
    if "Lap Distance" in columns and DISTANCE not in columns:  # e.g. an AC-named file
        columns[DISTANCE] = columns.pop("Lap Distance")
    get = lambda ch: columns[ch][1] if ch in columns else None  # noqa: E731

    # 1. What the rows are spaced by -> session time of each row
    reset_beacons: list[float] = []
    distance, distance_resets = (_unwrap_distance(get(DISTANCE)) if DISTANCE in columns else (None, []))
    if TIME not in columns and LAP_TIME in columns:  # a lap timer is the only clock: it's joined up below
        columns[TIME] = columns.pop(LAP_TIME)
    if TIME in columns and np.isfinite(get(TIME)).sum() >= 2:
        t, reset_beacons = _clean_time(get(TIME), columns[TIME][0])
        spacing = f"time column '{columns[TIME][0]}'"
    elif distance is not None and "Ground Speed" in columns:
        t = _time_from_distance(distance, get("Ground Speed"))
        spacing = f"distance column '{columns[DISTANCE][0]}' and speed (no time column)"
    else:
        raise CsvError(
            f"Can't tell how the rows are spaced: no time column (e.g. 'Time', 'Timestamp'), and no distance column "
            f"with a speed column. Columns found: {', '.join(layout.names)}. If one of them is the time, set its "
            f"\"channel\" to \"Time\" in {store.path}."
        )
    ok = np.isfinite(t)
    if ok.sum() < 2:
        raise CsvError("Fewer than two rows have a time, so there's nothing to plot")
    # The app's clock starts at 0. A file starting a moment after 0 (e.g. a lap from the
    # app's CSV download) keeps its times; any other start (a clock time, a long session) moves to 0.
    first = float(t[ok][0])
    t0 = 0.0 if 0 <= first < 1.0 else first
    t = t - t0
    reset_beacons = [b - t0 for b in reset_beacons]

    # 2. One even clock for every channel, at the file's typical row rate
    freq = int(np.clip(round(1.0 / float(np.median(np.diff(t[ok])))), 1, 1000))
    n = int(np.floor(t[ok][-1] * freq)) + 1
    if n > MAX_SAMPLES:
        raise CsvError(f"The rows cover {t[ok][-1]:.0f} s (from the {spacing}) - is the time really in seconds? "
                       f"Set the unit (e.g. ms) in {store.path}")

    channels: dict[str, Channel] = {}

    def add(name: str, unit: str, values: np.ndarray, description: str) -> None:
        # float32 halves the memory, like the iRacing reader
        channels[name] = Channel(name, unit, freq, np.asarray(values, dtype=np.float32), description=description)

    skip = {TIME, DISTANCE, LAT, LON}
    for channel, (name, values, unit) in columns.items():
        if channel in skip:
            continue
        known = channel in APP_UNITS
        on_clock = _on_clock(t, values, n, freq, _is_discrete(values, unit))
        if on_clock is None or (not known and np.all(on_clock == on_clock[0])):
            continue  # nothing to plot (a column we don't know that never changes)
        if channel == LAP_TIME and not _looks_like_lap_timer(on_clock, freq):
            channel = name if name != LAP_TIME else f"{name} (column)"  # e.g. a "last lap time": not a running timer
        add(channel, unit, on_clock, f"From CSV column '{name}'")

    # 3. Positions for the track map
    kph = channels["Ground Speed"].data.astype(np.float64) if "Ground Speed" in channels else None
    if LAT in columns and LON in columns:
        lat, lon = _on_clock(t, get(LAT), n, freq, False), _on_clock(t, get(LON), n, freq, False)
        xy = _positions(lat, lon, None) if lat is not None and lon is not None else None
        if xy is not None:
            add("Car Coord X", "m", xy[0], f"Metres east of the middle of the run (from GPS column '{columns[LON][0]}')")
            add("Car Coord Y", "m", xy[1], f"Metres north of the middle of the run (from GPS column '{columns[LAT][0]}')")

    # 4. Distance covered since the start of the file, for lining laps up
    if distance is not None:
        run_d = _on_clock(t, distance, n, freq, False)
        d_source = f"distance column '{columns[DISTANCE][0]}'"
    elif kph is not None:
        run_d = np.concatenate([[0.0], np.cumsum((kph[1:] + kph[:-1]) / 2 / 3.6 / freq)])
        d_source = "speed (added up over time)"
    elif "Car Coord X" in channels:
        x, y = channels["Car Coord X"].data, channels["Car Coord Y"].data
        run_d = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
        d_source = "GPS positions"
    else:
        raise CsvError("Laps are lined up by distance, so the file needs a speed, distance or GPS column "
                       f"(columns found: {', '.join(layout.names)})")

    # 5. Laps, best source first
    duration = n / freq
    beacons, lap_source = [], "none found: the file is one run"
    meta_beacons = _meta(layout.meta, "beacon markers", "beacons")
    lap_col = get(LAP)
    if meta_beacons:
        beacons = [float(x) - t0 for x in re.split(r"[\s;,]+", meta_beacons) if _is_number(x)]
        lap_source = "beacon markers in the file's header"
    if not beacons and lap_col is not None:
        rows = np.flatnonzero(np.isfinite(lap_col) & np.isfinite(t))
        rises = rows[1:][np.diff(lap_col[rows]) > 0]
        beacons = [float(t[i]) for i in rises]
        lap_source = f"lap column '{columns[LAP][0]}'"
    if not beacons and reset_beacons:
        beacons, lap_source = reset_beacons, f"time column '{columns[TIME][0]}' going back to 0"
    if not beacons and LAP_TIME in channels:
        lt = channels[LAP_TIME].data
        beacons = [i / freq for i in np.flatnonzero((np.diff(lt) < -1.0) & (lt[1:] < 1.0)) + 1]
        lap_source = "lap timer going back to 0"
    if not beacons and distance_resets:
        beacons = [float(t[i]) for i in distance_resets if np.isfinite(t[i])]
        lap_source = f"distance column '{columns[DISTANCE][0]}' going back to 0"
    if not beacons and "Car Coord X" in channels:
        beacons = _gps_returns(channels["Car Coord X"].data, channels["Car Coord Y"].data, freq, kph)
        lap_source = "GPS: each time the car came back past where it set off"
    beacons = sorted(b for b in beacons if 0 < b < duration)
    if not beacons:
        lap_source = "none found: the file is one run"

    add("Lap Distance", "m", _lap_distance(run_d, beacons, freq), f"Distance into the lap, from {d_source}")

    date, clock = _date_time(layout.meta, path)
    filled = lambda key, *meta_keys, default: (  # noqa: E731
        str(entry.get(key) or "").strip() or _meta(layout.meta, *meta_keys) or default)
    details = {
        **{f"CSV {k}": v for k, v in layout.meta.items()},
        "CSV Source": signature,
        "CSV Mapping File": str(store.path),
        "CSV Rows Spaced By": spacing,
        "CSV Laps From": lap_source,
        "CSV Rate": f"{freq} Hz",
    }
    return LdSession(
        path=path,
        driver=filled("driver", "driver", "racer", default="Unknown driver"),
        car=filled("car", "vehicle", "car", default="Unknown car"),
        track=filled("track", "venue", "track", "circuit", default="Unknown track"),
        date=date,
        time=clock,
        channels=channels,
        beacons=beacons,
        details=details,
        source="csv",
    )


def _looks_like_lap_timer(values: np.ndarray, freq: int) -> bool:
    """A running lap timer goes up by about one second per second (between resets)."""
    rate = np.diff(values) * freq
    return len(rate) > 0 and 0.8 < float(np.median(rate)) < 1.2
