"""Saved chart layouts ("channel sets").

A layout is a name plus a list of chart rows ("panels"); each panel lists the
channels drawn on top of each other in that row. For example:

    "Tyre temps": [["Tire Temp Core FL", "Tire Temp Core FR", ...],   <- one row, 4 lines
                   ["Tire Pressure FL", ...]]                          <- second row

Built-in layouts live in this file. Layouts people save from the dashboard go
into data/layouts.json on the server, so everyone using the server shares them.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from .config import DATA_DIR

MAX_PANELS = 12
MAX_CHANNELS_PER_PANEL = 8  # matches the 8 chart colours

Panels = list[list[str]]


def _four(prefix: str) -> list[str]:
    return [f"{prefix} {corner}" for corner in ("FL", "FR", "RL", "RR")]


BUILT_IN: dict[str, Panels] = {
    "Driver inputs": [["Ground Speed"], ["Throttle Pos", "Brake Pos"], ["Steering Angle"], ["Gear"], ["Engine RPM"]],
    "Balance": [["Ground Speed"], ["Steering Angle"], ["Chassis Yaw Rate"], ["CG Accel Lateral", "CG Accel Longitudinal"]],
    "Tyre temps": [_four("Tire Temp Core"), _four("Tire Temp Inner"), _four("Tire Temp Outer"), _four("Tire Pressure")],
    "Tyre slip": [["Tire Slip Angle FL", "Tire Slip Angle FR"], ["Tire Slip Angle RL", "Tire Slip Angle RR"], _four("Tire Slip Ratio")],
    "Tyre wear": [_four("VirtualKm"), _four("Tire Rubber Grip")],
    "Suspension": [_four("Suspension Travel"), ["Ride Height Front", "Ride Height Rear"], ["Chassis Roll Angle"], ["Chassis Pitch Angle"]],
    "Brakes": [["Ground Speed"], ["Brake Pos"], _four("Brake Temp"), _four("Brake Torque")],
    # maths channels (app/maths.py)
    "Balance & grip": [["Ground Speed"], ["G Absolute"], ["Balance Slip Angle"], ["Yaw Rate Excess"],
                       ["Rear Wheelspin", "Front Lockup"], ["Throttle Brake Overlap"]],
    "Tyre spread & rake": [_four("Tire Temp Spread"), ["Rake"], ["Brake Balance Actual"]],
    "Aero (estimated)": [["Ground Speed"], ["Aero Downforce (est.)", "Aero Downforce Front (est.)", "Aero Downforce Rear (est.)"],
                         ["Aero Balance (est.)"], ["Downforce Coefficient (est.)"], ["Ride Height Front", "Ride Height Rear"]],
}


# Channels that were renamed: saved layouts using the old name get the new one
RENAMED = {"Combined G": "G Absolute"}


def _renamed(panels: Panels) -> Panels:
    return [[RENAMED.get(c, c) for c in row] for row in panels]


class Layout(BaseModel):
    name: str
    panels: Panels
    built_in: bool = False


class LayoutIn(BaseModel):
    """What the dashboard sends when saving. Field limits are checked by FastAPI
    automatically - a bad request gets a 422 error explaining what's wrong."""

    panels: list[list[str]] = Field(min_length=1, max_length=MAX_PANELS)


class LayoutStore:
    def __init__(self, path: Path | None = None):
        self.path = path or DATA_DIR / "layouts.json"

    def _read_saved(self) -> dict[str, Panels]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write_saved(self, saved: dict[str, Panels]) -> None:
        # Write to a temporary file then swap it in, so a crash mid-write
        # can never leave a half-written (corrupt) layouts.json.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(saved, f, indent=2)
        os.replace(tmp, self.path)

    def all(self) -> list[Layout]:
        built_in = [Layout(name=n, panels=p, built_in=True) for n, p in BUILT_IN.items()]
        saved = [Layout(name=n, panels=_renamed(p)) for n, p in sorted(self._read_saved().items())]
        return built_in + saved

    def save(self, name: str, panels: Panels) -> Layout:
        if name in BUILT_IN:
            raise PermissionError(f"'{name}' is a built-in layout; save under another name")
        panels = [p for p in panels if p]  # drop empty rows
        if any(len(p) > MAX_CHANNELS_PER_PANEL for p in panels):
            raise ValueError(f"A row can hold at most {MAX_CHANNELS_PER_PANEL} channels")
        if not panels:
            raise ValueError("A layout needs at least one channel")
        saved = self._read_saved()
        saved[name] = panels
        self._write_saved(saved)
        return Layout(name=name, panels=panels)

    def delete(self, name: str) -> bool:
        if name in BUILT_IN:
            raise PermissionError(f"'{name}' is a built-in layout and can't be deleted")
        saved = self._read_saved()
        if saved.pop(name, None) is None:
            return False
        self._write_saved(saved)
        return True
