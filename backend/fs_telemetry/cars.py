"""Facts about each car that the telemetry doesn't record: steering ratio and wheelbase.

The Understeer Angle maths channel needs both. They come from the best place
available, so for AC cars nobody has to type anything in:
  1. The CARS table below, if the car is in it (overrides everything else).
  2. The car's own AC files (data/car.ini and data/suspensions.ini), if the
     car is installed and its data folder isn't packed into data.acd. Looked
     for in CARS_DIRS (config.py): the app's "cars" folder, then the AC install.
  3. Steering ratio only: the iRacing setup, for the few cars whose setup has
     one (e.g. "SteeringRatio: 12:1" on the Street Stock).
  4. Wheelbase only: AC's .ldx file, which always has it.
Known numbers are always used first. If they aren't known, maths.py learns
what it can from the run itself, and says so (see maths.car_channels):
the steering ratio from an AC run's slip angles, or for iRacing, the steering
a corner needs from the run's gentle corners. To make an iRacing car use known
numbers, add a row to the table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import CARS_DIRS
from .ingest.motec_ld import LdSession


@dataclass
class Car:
    steering_ratio: float | None = None  # steering-wheel degrees per degree at the front wheels
    wheelbase_m: float | None = None


# Use the exact car name the app shows: AC's car folder name (e.g. "madformulateam_mft02"),
# or iRacing's car name (e.g. "Ray Formula 1600"). Leave a number out to use the automatic one.
CARS: dict[str, Car] = {
    # "Ray Formula 1600": Car(steering_ratio=12.0, wheelbase_m=2.3),
}


@dataclass
class CarFacts:
    steering_ratio: float | None
    wheelbase_m: float | None
    ratio_from: str  # where the steering ratio came from, said in the channel description
    wheelbase_from: str = ""  # ...and the wheelbase


def _ini_number(path: Path, key: str) -> float | None:
    """A number from an AC .ini file, e.g. STEER_RATIO=12.5 (some cars write it negative)."""
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None
    m = re.search(rf"^\s*{key}\s*=\s*(-?\d+(?:\.\d+)?)", text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return None
    return abs(float(m.group(1))) or None  # 0 counts as "not given"


def read_ini(text: str) -> dict[str, dict[str, str]]:
    """AC .ini text -> {SECTION: {KEY: value}}, ignoring ';' comments. Later duplicates win."""
    out: dict[str, dict[str, str]] = {}
    section = None
    for line in text.splitlines():
        line = line.split(";", 1)[0].strip()
        if m := re.fullmatch(r"\[([^\]]+)\]", line):
            section = out.setdefault(m.group(1).strip().upper(), {})
        elif section is not None and "=" in line:
            k, v = line.split("=", 1)
            section[k.strip().upper()] = v.strip()
    return out


def ac_data_dir(car: str) -> Path | None:
    """The car's unpacked AC data folder (first match in CARS_DIRS), or None."""
    if not car or any(c in car for c in "/\\:") or ".." in car:
        return None  # a car name, not a path
    for folder in CARS_DIRS:
        data = folder / car / "data"
        if data.is_dir():
            return data
    return None


def from_ac_files(car: str) -> Car | None:
    """Steering ratio and wheelbase from the car's AC files, if they can be read."""
    data = ac_data_dir(car)
    if data is None:
        return None
    return Car(_ini_number(data / "car.ini", "STEER_RATIO"), _ini_number(data / "suspensions.ini", "WHEELBASE"))


def car_facts(s: LdSession) -> CarFacts:
    table = CARS.get(s.car, Car())
    files = (from_ac_files(s.car) if s.source == "ac" else None) or Car()
    try:
        ldx_wheelbase = float(s.details.get("Vehicle Wheelbase", "")) or None
    except ValueError:
        ldx_wheelbase = None

    if table.steering_ratio:
        ratio, ratio_from = table.steering_ratio, "cars.py"
    elif files.steering_ratio:
        ratio, ratio_from = files.steering_ratio, "the car's AC files"
    elif setup_ratio := iracing_setup_ratio(s):
        ratio, ratio_from = setup_ratio, "the car's iRacing setup"
    else:
        ratio, ratio_from = None, ""
    wheelbase, wheelbase_from = next(
        ((w, where) for w, where in ((table.wheelbase_m, "cars.py"), (files.wheelbase_m, "the car's AC files"),
                                     (ldx_wheelbase, "the .ldx")) if w),
        (None, ""))
    return CarFacts(ratio, wheelbase, ratio_from, wheelbase_from)


def iracing_setup_ratio(s: LdSession) -> float | None:
    """The steering ratio from an iRacing setup that has one ("CarSetup_..._SteeringRatio": "12:1")."""
    for key, value in s.details.items():
        if key.startswith("CarSetup_") and key.endswith("SteeringRatio"):
            m = re.match(r"\s*(\d+(?:\.\d+)?)", value)
            if m and float(m.group(1)) > 0:
                return float(m.group(1))
    return None
