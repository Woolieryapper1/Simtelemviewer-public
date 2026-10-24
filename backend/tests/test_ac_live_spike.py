"""The live AC shared-memory spike (backend/scripts/ac_live_spike.py): struct offsets, and reading a
page read only. The tests make their own page with a made-up name; they never touch AC's."""

import ctypes
import importlib.util
import math
import mmap
import sys
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ac_live_spike.py"
spec = importlib.util.spec_from_file_location("ac_live_spike", SCRIPT)
spike = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spike)

windows = pytest.mark.skipif(sys.platform != "win32", reason="named shared memory is Windows only")


def test_physics_offsets_match_the_c_header():
    """Byte offsets worked out by hand from SharedFileOut.h (every field 4 bytes)."""
    P = spike.Physics
    assert (P.wheelLoad.offset, P.tyreWear.offset, P.camberRAD.offset) == (72, 120, 168)
    assert (P.accG.offset, P.speedKmh.offset, P.tyreCoreTemperature.offset) == (44, 28, 152)
    assert (P.tyreTempI.offset, P.tyreTempM.offset, P.tyreTempO.offset) == (368, 384, 400)
    assert ctypes.sizeof(P) == 580
    assert spike.Static.carModel.offset == 68 and ctypes.sizeof(spike.Static) == 200


def fake_physics():
    p = spike.Physics(packetId=1234, speedKmh=88.5)
    p.accG[:] = (1.5, 1.0, -0.4)
    for i in range(4):
        p.camberRAD[i] = math.radians(-2.0 - i)
        p.wheelLoad[i] = 3000 + 100 * i
        p.tyreWear[i] = 99.5 - i
        p.tyreTempI[i], p.tyreTempM[i], p.tyreTempO[i], p.tyreCoreTemperature[i] = 85, 80, 75, 82
    return p


def test_wheels_in_degrees_and_newtons():
    w = spike.wheels(fake_physics())
    assert list(w) == ["FL", "FR", "RL", "RR"]
    assert w["RL"]["camber_deg"] == pytest.approx(-4.0, abs=1e-5)
    assert w["FR"]["load_n"] == 3100 and w["RR"]["wear"] == pytest.approx(96.5)


def test_camber_grip_peaks_at_the_parabola_top():
    d0, d1 = 1.4, -13
    peak = -math.degrees(d0 / (2 * abs(d1)))
    assert spike.camber_grip_pct(peak, d0, d1) == pytest.approx(100)
    assert spike.camber_grip_pct(peak + 1, d0, d1) < 100 > spike.camber_grip_pct(peak - 1, d0, d1)


def test_frame_text_has_every_wheel_and_grip_when_asked():
    text = spike.format_frame(fake_physics(), {"F": (1.4, -13)})
    assert "88.5 km/h" in text and "G lat +1.50 lon -0.40" in text
    lines = text.splitlines()
    assert [l.split()[0] for l in lines[1:]] == ["FL", "FR", "RL", "RR"]
    assert "grip" in lines[1] and "grip" not in lines[3]  # rear wasn't given


@windows
def test_reads_a_page_and_maps_it_read_only():
    name = f"Local\\fs_telemetry_test_{uuid.uuid4().hex}"
    size = ctypes.sizeof(spike.Physics)
    with mmap.mmap(-1, size, tagname=name) as owner:  # the test's own page, standing in for AC
        owner.write(bytes(fake_physics()))
        with spike.SharedPage(name, spike.Physics) as page:
            p = page.read()
            assert p.packetId == 1234 and p.wheelLoad[3] == 3300

            class Info(ctypes.Structure):  # MEMORY_BASIC_INFORMATION
                _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                            ("AllocationProtect", ctypes.c_ulong), ("PartitionId", ctypes.c_ushort),
                            ("RegionSize", ctypes.c_size_t), ("State", ctypes.c_ulong),
                            ("Protect", ctypes.c_ulong), ("Type", ctypes.c_ulong)]

            info = Info()
            k = ctypes.WinDLL("kernel32")
            k.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
            assert k.VirtualQuery(page._view, ctypes.byref(info), ctypes.sizeof(info))
            assert info.Protect == 0x02  # PAGE_READONLY: Windows would refuse a write

            owner.seek(0)
            owner.write(ctypes.c_int32(1235))  # AC moves on; read() is a fresh copy each time
            assert page.read().packetId == 1235 and p.packetId == 1234


@windows
def test_missing_page_is_not_created():
    name = f"Local\\fs_telemetry_test_{uuid.uuid4().hex}"
    for _ in range(2):  # the first try must not have made it exist
        with pytest.raises(spike.NotRunning, match="is AC running"):
            spike.SharedPage(name, spike.Physics)
