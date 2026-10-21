"""Read MoTeC i2 log files (.ld + .ldx) exported from Assetto Corsa.

Two AC loggers write these, two files per session:
  * <name>.ld  - binary file with every logged channel (speed, throttle, tyre temps...)
  * <name>.ldx - small XML file with lap beacons (the times each lap was completed)
The telemetrick app's files are what the rest of the app expects. Custom Shaders
Patch's built-in logger names channels differently and doesn't always write
beacons, so its files are converted right after reading (see ingest/csp.py).

MoTeC never published the .ld format. The layout below follows the community
reverse-engineering in https://github.com/gotzl/ldparser, and was checked against
our own MFT02 exports.

Layout of a .ld file, in plain terms:
  1. A fixed-size header at the start: driver, car, track, date, and a pointer
     ("go to byte N") to the first channel description.
  2. A chain of channel descriptions. Each one says: my name, my unit, how many
     samples I have, my sample rate (Hz), where my data lives in the file, and
     the pointer to the next channel description (0 = end of chain).
  3. The raw sample data for every channel.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import numpy as np

# --- Binary layouts -------------------------------------------------------
# struct format codes: I = 4-byte unsigned int, H = 2-byte unsigned, h = 2-byte signed,
# Ns = N-byte text, Nx = skip N bytes we don't understand. '<' = little-endian.

# Header: we only read the fields we need.
_HEAD_PTRS = struct.Struct("<I4xII")  # marker, channel_meta_ptr, channel_data_ptr
_HEAD_INFO_OFFSET = struct.calcsize("<I4xII20xI24xHHHI8sHHI4x")
_HEAD_INFO = struct.Struct("<16s16x16s16x64s64s64x64s")  # date, time, driver, car, (skip), track

# One channel description (a "node" in the chain).
_CHANNEL = struct.Struct(
    "<IIII"  # prev_ptr, next_ptr, data_ptr, n_samples
    "H"  # counter (unused)
    "HH"  # data kind, bytes per sample
    "H"  # sample rate in Hz
    "hhhh"  # shift, mul, scale, decimal places  -> used to convert raw numbers to real units
    "32s8s12s"  # name, short name, unit
)

# (kind, bytes) -> numpy type. Kind 7 = floating point, 0/3/5 = integer.
_DTYPES = {
    (7, 2): np.float16,
    (7, 4): np.float32,
    (0, 2): np.int16,
    (0, 4): np.int32,
    (3, 2): np.int16,
    (3, 4): np.int32,
    (5, 2): np.int16,
    (5, 4): np.int32,
}


def _text(raw: bytes) -> str:
    """Fixed-length text fields are padded with zero bytes; strip them."""
    return raw.split(b"\0", 1)[0].decode("latin-1").strip()


# --- Data classes ---------------------------------------------------------


@dataclass
class Channel:
    name: str
    unit: str
    freq: int  # samples per second
    data: np.ndarray  # values already converted to real units
    maths: bool = False  # True for channels we calculate (see app/maths.py), not logged ones
    approximate: bool = False  # True for estimates (e.g. aero loads), not exact calculations
    description: str = ""
    basis: str = ""  # for calculated channels: what the numbers rest on, e.g. "known: steering ratio 12:1 (cars.py)"

    @property
    def time(self) -> np.ndarray:
        """Session time (s) of each sample. Sample i is at i / freq."""
        return np.arange(len(self.data)) / self.freq


@dataclass
class Lap:
    number: int  # 1 = out lap
    start: float  # session time, seconds
    end: float

    @property
    def lap_time(self) -> float:
        return self.end - self.start


@dataclass
class LdSession:
    path: Path
    driver: str
    car: str
    track: str
    date: str
    time: str
    channels: dict[str, Channel]
    beacons: list[float] = field(default_factory=list)  # lap-line crossings, seconds
    # Moments the run was cut off mid-lap (CSP logs on after "back to pits"; telemetrick
    # starts a new file): the lap so far ends there unfinished and an out lap starts
    breaks: list[float] = field(default_factory=list)
    details: dict[str, str] = field(default_factory=dict)  # from the .ldx, e.g. "Vehicle Wheelbase": "1.545"
    source: str = "ac"  # which sim it came from: "ac" (this reader) or "iracing" (ingest/ibt.py)
    sector_lines_m: list[float] = field(default_factory=list)  # official sector lines, if the file says (iRacing does)

    @property
    def duration(self) -> float:
        return max(len(c.data) / c.freq for c in self.channels.values())

    @cached_property
    def laps(self) -> list[Lap]:
        """Split the session at each beacon (and break). First lap is the out lap; the last
        one is usually incomplete (in lap / session ended mid-lap)."""
        edges = [0.0, *sorted({*self.beacons, *self.breaks}), self.duration]
        return [Lap(i + 1, a, b) for i, (a, b) in enumerate(zip(edges, edges[1:]))]


# --- Readers --------------------------------------------------------------


def read_ldx_beacons(path: Path) -> list[float]:
    """Lap beacon times (seconds) from the .ldx file. Stored in microseconds."""
    if not path.exists():
        return []
    root = ET.parse(path).getroot()
    times = [float(m.get("Time")) / 1e6 for m in root.iter("Marker") if m.get("ClassName") == "BCN"]
    return sorted(times)


def read_ldx_details(path: Path) -> dict[str, str]:
    """Session details MoTeC stores in the .ldx (vehicle wheelbase, weight, fastest lap...)."""
    if not path.exists():
        return {}
    root = ET.parse(path).getroot()
    return {e.get("Id"): e.get("Value") for e in root.iter() if e.tag in ("String", "Numeric") and e.get("Id")}


def read_ld(path: str | Path) -> LdSession:
    path = Path(path)
    buf = path.read_bytes()

    _marker, meta_ptr, _data_ptr = _HEAD_PTRS.unpack_from(buf, 0)
    date, time, driver, car, track = (_text(x) for x in _HEAD_INFO.unpack_from(buf, _HEAD_INFO_OFFSET))

    channels: dict[str, Channel] = {}
    ptr = meta_ptr
    seen = set()  # guard against a corrupt file looping forever
    while ptr and ptr not in seen:
        seen.add(ptr)
        (_prev, next_ptr, data_ptr, n, _cnt, kind, size, freq,
         shift, mul, scale, dec, name, _short, unit) = _CHANNEL.unpack_from(buf, ptr)

        dtype = _DTYPES.get((kind, size))
        if dtype is not None and n > 0 and freq > 0:
            raw = np.frombuffer(buf, dtype=dtype, count=n, offset=data_ptr)
            # MoTeC stores scaled numbers; convert back to real units.
            values = (raw.astype(np.float64) / scale * 10.0 ** -dec + shift) * mul
            ch = Channel(_text(name), _text(unit), freq, values)
            channels[ch.name] = ch
        ptr = next_ptr

    session = LdSession(
        path=path, driver=driver, car=car, track=track, date=date, time=time,
        channels=channels, beacons=read_ldx_beacons(path.with_suffix(".ldx")),
        details=read_ldx_details(path.with_suffix(".ldx")),
    )
    from .csp import adapt_csp_log, is_csp_log  # here, not at the top: csp.py builds on this module

    return adapt_csp_log(session) if is_csp_log(session) else session
