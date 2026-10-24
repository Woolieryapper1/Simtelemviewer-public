"""Spike: print Assetto Corsa's live camber, tyre load and tyre wear per wheel while you drive.

Not part of the app yet: it proves the backend can read AC's shared memory on its own,
with no in-game app. Start AC, get on track, then (from the repo folder):
    .venv\\Scripts\\python backend\\scripts\\ac_live_spike.py
    .venv\\Scripts\\python backend\\scripts\\ac_live_spike.py --hz 20 --seconds 60
    .venv\\Scripts\\python backend\\scripts\\ac_live_spike.py --front 1.4 -13 --rear 1.66 -13   # + grip vs tyres.ini

Read only. AC creates three named memory pages (Local\\acpmf_physics, acpmf_graphics,
acpmf_static). This opens them with OpenFileMappingW(FILE_MAP_READ): Windows refuses any
write through the view, and if AC isn't running it fails instead of creating an empty page
(Python's mmap.mmap(0, n, "acpmf_physics"), which most AC apps use, opens them read-write
and creates them when missing).

Layout: SPageFilePhysics / SPageFileGraphic / SPageFileStatic as in Kunos' SharedFileOut.h,
copied from the sim_info.py "updated to AC 1.14.3" that ships with the LiveTelemetry app (the
ACTI app's copy matches). All fields are 4 bytes (wchar_t strings are 2), so _pack_ = 4 adds
no padding. Only the start of each page is declared: later AC versions add fields at the end,
which leaves these offsets unchanged.
"""

from __future__ import annotations

import argparse
import ctypes
import math
import sys
import time
from ctypes import c_float, c_int32, c_wchar, wintypes

WHEELS = ("FL", "FR", "RL", "RR")
AC_STATUS = {0: "off", 1: "replay", 2: "live", 3: "pause"}


class Physics(ctypes.Structure):
    """SPageFilePhysics, up to localVelocity (AC 1.14.3)."""

    _pack_ = 4
    _fields_ = [
        ("packetId", c_int32),
        ("gas", c_float),
        ("brake", c_float),
        ("fuel", c_float),
        ("gear", c_int32),
        ("rpms", c_int32),
        ("steerAngle", c_float),
        ("speedKmh", c_float),
        ("velocity", c_float * 3),
        ("accG", c_float * 3),  # G: [0] lateral, [1] vertical, [2] longitudinal (AC's axes, y up)
        ("wheelSlip", c_float * 4),
        ("wheelLoad", c_float * 4),  # N
        ("wheelsPressure", c_float * 4),  # psi
        ("wheelAngularSpeed", c_float * 4),
        ("tyreWear", c_float * 4),  # see the design note: looks like tyres.ini WEAR_CURVE grip %, ~100 = new
        ("tyreDirtyLevel", c_float * 4),
        ("tyreCoreTemperature", c_float * 4),  # °C
        ("camberRAD", c_float * 4),  # radians
        ("suspensionTravel", c_float * 4),
        ("drs", c_float),
        ("tc", c_float),
        ("heading", c_float),
        ("pitch", c_float),
        ("roll", c_float),
        ("cgHeight", c_float),
        ("carDamage", c_float * 5),
        ("numberOfTyresOut", c_int32),
        ("pitLimiterOn", c_int32),
        ("abs", c_float),
        ("kersCharge", c_float),
        ("kersInput", c_float),
        ("autoShifterOn", c_int32),
        ("rideHeight", c_float * 2),
        ("turboBoost", c_float),
        ("ballast", c_float),
        ("airDensity", c_float),
        ("airTemp", c_float),
        ("roadTemp", c_float),
        ("localAngularVel", c_float * 3),
        ("finalFF", c_float),
        ("performanceMeter", c_float),
        ("engineBrake", c_int32),
        ("ersRecoveryLevel", c_int32),
        ("ersPowerLevel", c_int32),
        ("ersHeatCharging", c_int32),
        ("ersIsCharging", c_int32),
        ("kersCurrentKJ", c_float),
        ("drsAvailable", c_int32),
        ("drsEnabled", c_int32),
        ("brakeTemp", c_float * 4),
        ("clutch", c_float),
        ("tyreTempI", c_float * 4),  # °C, inner / middle / outer tread
        ("tyreTempM", c_float * 4),
        ("tyreTempO", c_float * 4),
        ("isAIControlled", c_int32),
        ("tyreContactPoint", c_float * 3 * 4),
        ("tyreContactNormal", c_float * 3 * 4),
        ("tyreContactHeading", c_float * 3 * 4),
        ("brakeBias", c_float),
        ("localVelocity", c_float * 3),
    ]


class Graphics(ctypes.Structure):
    """Start of SPageFileGraphic: enough to tell live from paused or a replay."""

    _pack_ = 4
    _fields_ = [("packetId", c_int32), ("status", c_int32), ("session", c_int32)]


class Static(ctypes.Structure):
    """Start of SPageFileStatic: which car (to find its tyres.ini) and track."""

    _pack_ = 4
    _fields_ = [
        ("smVersion", c_wchar * 15),
        ("acVersion", c_wchar * 15),
        ("numberOfSessions", c_int32),
        ("numCars", c_int32),
        ("carModel", c_wchar * 33),
        ("track", c_wchar * 33),
    ]


# --- Opening a page, read only ------------------------------------------------------

FILE_MAP_READ = 0x0004


class NotRunning(RuntimeError):
    """The page doesn't exist: AC (or the thing that makes it) isn't running."""


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k.OpenFileMappingW.restype = wintypes.HANDLE
    k.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    k.MapViewOfFile.restype = wintypes.LPVOID
    k.UnmapViewOfFile.argtypes = [wintypes.LPCVOID]
    k.UnmapViewOfFile.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    return k


class SharedPage:
    """A named memory page opened read only. read() returns a copy, so the caller never holds
    a pointer into AC's memory."""

    def __init__(self, name: str, struct: type[ctypes.Structure]):
        self.name, self.struct, self.size = name, struct, ctypes.sizeof(struct)
        self._k = _kernel32()
        self._handle = self._k.OpenFileMappingW(FILE_MAP_READ, False, name)
        if not self._handle:
            raise NotRunning(f"No shared memory called {name} (Windows error {ctypes.get_last_error()}): is AC running?")
        self._view = self._k.MapViewOfFile(self._handle, FILE_MAP_READ, 0, 0, self.size)
        if not self._view:
            err = ctypes.get_last_error()
            self._k.CloseHandle(self._handle)
            raise OSError(f"Couldn't map {name} (Windows error {err})")

    def read(self):
        """A copy of the page. AC writes while we read, so the copy is kept only if packetId
        didn't change during it (a few tries, then the last copy)."""
        for _ in range(3):
            page = self.struct.from_buffer_copy(ctypes.string_at(self._view, self.size))
            if not hasattr(page, "packetId") or page.packetId == c_int32.from_address(self._view).value:
                break
        return page

    def close(self) -> None:
        if self._view:
            self._k.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle:
            self._k.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# --- What to show (plain functions, easy to test) ------------------------------------


def camber_grip_pct(camber_deg: float, d0: float, d1: float) -> float:
    """Grip from camber as % of the tyre's best, on tyres.ini's DCAMBER parabola
    f(γ) = 1 + D0·|γ| + D1·γ² (γ in rad), the same reading as the camber optimiser."""
    g, peak = abs(math.radians(camber_deg)), d0 / (2 * abs(d1))
    f = lambda x: max(1 + d0 * x + d1 * x * x, 0.0)
    return 100 * f(g) / f(peak)


def wheels(p: Physics) -> dict[str, dict[str, float]]:
    """Per wheel: camber (deg), load (N), raw tyreWear, tread temps inner/middle/outer and core (°C)."""
    return {w: {"camber_deg": math.degrees(p.camberRAD[i]), "load_n": p.wheelLoad[i], "wear": p.tyreWear[i],
                "temp_i": p.tyreTempI[i], "temp_m": p.tyreTempM[i], "temp_o": p.tyreTempO[i],
                "temp_core": p.tyreCoreTemperature[i]}
            for i, w in enumerate(WHEELS)}


def format_frame(p: Physics, tyres: dict[str, tuple[float, float]] | None = None) -> str:
    """One block of text per sample. tyres: {"F": (D0, D1), "R": (D0, D1)} adds camber grip %."""
    lat, _, lon = p.accG
    lines = [f"#{p.packetId}  {p.speedKmh:6.1f} km/h  G lat {lat:+5.2f} lon {lon:+5.2f}"]
    for w, v in wheels(p).items():
        line = (f"  {w}  camber {v['camber_deg']:+6.2f}°  load {v['load_n']:6.0f} N  wear {v['wear']:8.4f}"
                f"  I/M/O {v['temp_i']:5.1f}/{v['temp_m']:5.1f}/{v['temp_o']:5.1f}  core {v['temp_core']:5.1f} °C")
        if tyres and w[0] in tyres:
            line += f"  grip {camber_grip_pct(v['camber_deg'], *tyres[w[0]]):5.1f}%"
        lines.append(line)
    return "\n".join(lines)


# --- Run --------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hz", type=float, default=10, help="prints per second (default 10)")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long (default: until Ctrl+C)")
    ap.add_argument("--front", nargs=2, type=float, metavar=("D0", "D1"), help="front DCAMBER_0 DCAMBER_1 from tyres.ini")
    ap.add_argument("--rear", nargs=2, type=float, metavar=("D0", "D1"), help="rear DCAMBER_0 DCAMBER_1 from tyres.ini")
    args = ap.parse_args(argv)
    if sys.platform != "win32":
        print("AC's shared memory only exists on Windows.")
        return 1
    tyres = {k: tuple(v) for k, v in (("F", args.front), ("R", args.rear)) if v}

    try:
        physics = SharedPage("Local\\acpmf_physics", Physics)
        graphics = SharedPage("Local\\acpmf_graphics", Graphics)
        static = SharedPage("Local\\acpmf_static", Static)
    except NotRunning as e:
        print(e)
        return 1
    with physics, graphics, static:
        s = static.read()
        print(f"{s.carModel} at {s.track}  (AC {s.acVersion}, shared memory {s.smVersion})")
        start, last_id, last_t = time.monotonic(), None, None
        try:
            while not args.seconds or time.monotonic() - start < args.seconds:
                p, now = physics.read(), time.monotonic()
                rate = "" if last_id is None else f"  ({(p.packetId - last_id) / (now - last_t):.0f} physics packets/s)"
                last_id, last_t = p.packetId, now
                print(f"[{AC_STATUS.get(graphics.read().status, '?')}]{rate}")
                print(format_frame(p, tyres))
                time.sleep(1 / args.hz)
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
