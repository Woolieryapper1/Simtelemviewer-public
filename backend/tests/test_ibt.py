"""Tests for the iRacing .ibt reader. The first ones build a small .ibt file with
known answers, so they run anywhere; the real-data ones are skipped if there are
no .ibt files on this machine (set FS_IRACING_DIR to point somewhere else)."""

import struct

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fs_telemetry.ingest.ibt import parse_session_info, read_ibt
from fs_telemetry.laps import lap_infos
from fs_telemetry.maths import add_maths_channels

FREQ = 60
L = 1000.0  # fake track length, m
V = 47.0  # m/s - so the line is crossed between samples, not on one
START = 955.0  # m - the car starts in the pit lane, 45 m before the line
DURATION = 70.0  # s
OFF_TRACK = (50.0, 50.5)  # s - a moment off track during lap 3
PIT_UNTIL = 3.0  # s - in the pit lane until then (crossing the line at ~0.96 s)
DROPPED = 300  # this sample is missing, as iRacing occasionally does

SESSION_INFO = """---
WeekendInfo:
 TrackName: testtrack
 TrackDisplayName: Test Circuit
 TrackConfigName: Short
 TrackLength: 1.00 km
 TrackLatitude: 50.000000 m
 TrackLongitude: 0.000000 m
 EventType: Practice
DriverInfo:
 DriverCarIdx: 1
 DriverSetupName: test.sto
 DriverTires:
 - TireIndex: 0
   TireCompoundType: "Hard"
 Drivers:
 - CarIdx: 0
   UserName: Someone Else
   CarScreenName: Other Car
 - CarIdx: 1
   UserName: Test Driver
   CarPath: testcar
   CarScreenName: Test Car GT
SplitTimeInfo:
 Sectors:
 - SectorNum: 0
   SectorStartPct: 0.000000
 - SectorNum: 1
   SectorStartPct: 0.400000
 - SectorNum: 2
   SectorStartPct: 0.700000

CarSetup:
 UpdateCount: 2
 Tires:
  LeftFront:
   StartingPressure: 170 kPa
   LastHotPressure: 181 kPa
 Chassis:
  Front:
   ArbSetting: 2
   ToeIn: -1.0 mm
...
"""


def write_ibt(path, columns: dict, session_info: str, start_unix: int = 1_700_000_000):
    """columns: {name: (iRacing type number, unit, values)} -> an .ibt file, laid out as iRacing does."""
    kinds = {1: "?", 2: "<i4", 4: "<f4", 5: "<f8"}
    names = list(columns)
    dtype = np.dtype([(n, kinds[columns[n][0]]) for n in names])
    n = len(next(iter(columns.values()))[2])
    table = np.zeros(n, dtype)
    for name in names:
        table[name] = columns[name][2]
    info = session_info.encode("utf-8") + b"\0"
    vars_pos = 144
    info_pos = vars_pos + 144 * len(names)
    data_pos = info_pos + len(info)
    header = struct.pack("<10i2x2x4x", 2, 1, FREQ, 1, len(info), info_pos, len(names), vars_pos, 1, dtype.itemsize)
    header += struct.pack("<4i", n, data_pos, 0, 0) + bytes(16 * 3)
    disk = struct.pack("<qddii", start_unix, 0.0, DURATION, 0, n)
    var_headers = b"".join(
        struct.pack("<iii?3x32s64s32s", columns[name][0], dtype.fields[name][1], 1, False,
                    name.encode(), f"{name} description".encode(), columns[name][1].encode())
        for name in names
    )
    assert len(header) == 112 and len(disk) == 32
    path.write_bytes(header + disk + var_headers + info + table.tobytes())


@pytest.fixture(scope="module")
def fake_ibt(tmp_path_factory):
    t = np.arange(int(DURATION * FREQ)) / FREQ
    travelled = START + V * t
    dist = travelled % L
    lat = 50.0 + np.degrees(V * t / 6_371_000.0)  # driving due north
    lat[:10] = 0.0  # iRacing logs 0, 0 before the car is in the world
    lon = np.where(lat == 0, 0.0, 0.0)
    columns = {
        "SessionTime": (5, "s", 100.0 + t),
        "Lap": (2, "", np.floor(travelled / L)),
        "LapDist": (4, "m", dist),
        "LapDistPct": (4, "%", dist / L),
        "Speed": (4, "m/s", np.full_like(t, V)),
        "Throttle": (4, "%", np.full_like(t, 0.5)),
        "Brake": (4, "%", np.zeros_like(t)),
        "LatAccel": (4, "m/s^2", np.full_like(t, 9.81)),
        "VertAccel": (4, "m/s^2", np.full_like(t, 9.81)),
        "YawRate": (4, "rad/s", np.zeros_like(t)),
        "LFspeed": (4, "m/s", np.full_like(t, V)),
        "LFtempL": (4, "C", 80 + t),
        "RFtempL": (4, "C", 70 + t),
        "OnPitRoad": (1, "", t < PIT_UNTIL),
        "PlayerTrackSurface": (2, "irsdk_TrkLoc", np.where((t >= OFF_TRACK[0]) & (t < OFF_TRACK[1]), 0, 3)),
        "Lat": (5, "deg", lat),
        "Lon": (5, "deg", lon),
        "FuelLevelPct": (4, "%", 0.5 - t / 1000),
        "CpuUsageFG": (4, "%", np.random.default_rng(0).random(len(t))),  # PC load: not kept
        "Constant": (4, "", np.ones_like(t)),  # never changes: not kept
    }
    keep = np.arange(len(t)) != DROPPED
    columns = {k: (kind, unit, np.asarray(v)[keep]) for k, (kind, unit, v) in columns.items()}
    path = tmp_path_factory.mktemp("ibt") / "testcar_testtrack 2023-11-14 22-13-20.ibt"
    write_ibt(path, columns, SESSION_INFO)
    return path


def test_session_info_and_names(fake_ibt):
    s = read_ibt(fake_ibt)
    assert (s.driver, s.car, s.track, s.source) == ("Test Driver", "Test Car GT", "Test Circuit - Short", "iracing")
    assert s.details["CarSetup_Chassis_Front_ArbSetting"] == "2"
    assert s.details["Setup Name"] == "test.sto"
    assert s.sector_lines_m == pytest.approx([400, 700], abs=1)


def test_channels_are_renamed_and_converted(fake_ibt):
    c = read_ibt(fake_ibt).channels
    assert c["Ground Speed"].unit == "kph" and c["Ground Speed"].data[0] == pytest.approx(V * 3.6)
    assert c["Throttle Pos"].data[0] == pytest.approx(50)
    assert c["CG Accel Lateral"].data[0] == pytest.approx(-1.0)  # sign flipped to AC's convention
    assert c["CG Accel Vertical"].data[0] == pytest.approx(0.0, abs=1e-6)  # gravity taken out
    assert c["Tire Temp Outer FL"].data[0] == pytest.approx(80)  # left tyre: its left edge is the outside
    assert c["Tire Temp Inner FR"].data[0] == pytest.approx(70)  # right tyre: its left edge is the inside
    assert c["FuelLevelPct"].data[0] == pytest.approx(50)  # iRacing's 0..1 "%" -> real %
    assert "CpuUsageFG" not in c and "Constant" not in c and "Lat" not in c


def test_dropped_sample_is_filled_so_the_clock_stays_even(fake_ibt):
    c = read_ibt(fake_ibt).channels
    assert len(c["Lap Distance"].data) == int(DURATION * FREQ)
    i = 2000  # well after the gap: sample i is still at i / 60 s
    assert c["Lap Distance"].data[i] == pytest.approx((START + V * i / FREQ) % L, abs=0.01)


def test_laps_from_the_lap_counter_ignoring_the_pit_lane_crossing(fake_ibt):
    s = read_ibt(fake_ibt)
    crossings = [(k * L - START) / V for k in (2, 3, 4)]  # k=1 is in the pit lane
    assert s.beacons == pytest.approx(crossings, abs=1e-4)  # between samples, to well under a ms
    infos = lap_infos(s)
    assert [l.kind for l in infos] == ["out", "timed", "timed", "incomplete"]
    assert [l.invalid for l in infos[1:3]] == [False, True]  # lap 3 went off track
    assert infos[1].lap_time == pytest.approx(L / V, abs=1e-4)


def test_gps_becomes_metres_on_the_map(fake_ibt):
    c = read_ibt(fake_ibt).channels
    y = c["Car Coord Y"].data
    assert y[1200] == pytest.approx(V * 1200 / FREQ, abs=0.5)  # metres north of the track's reference point
    assert y[0] == pytest.approx(y[10], abs=1e-3)  # the 0, 0 samples take the first real position
    assert np.all(np.abs(c["Car Coord X"].data) < 0.01)


def test_slip_ratio_estimate_is_flagged_approximate(fake_ibt):
    c = add_maths_channels(read_ibt(fake_ibt)).channels
    slip = c["Tire Slip Ratio FL"]
    assert slip.maths and slip.approximate
    assert np.allclose(slip.data, 0, atol=1e-3)  # wheel turning at road speed: no slip


def test_setup_is_imported_from_a_run(fake_ibt, tmp_path):
    from fs_telemetry.setups import SetupLibrary, group_of, pretty

    lib = SetupLibrary(tmp_path / "library.json", tmp_path / "no_ac", fake_ibt.parent)
    (available,) = lib.available()
    assert (available.source, available.car, available.track, available.name) == \
        ("iracing", "Test Car GT", "Test Circuit - Short", "test")
    setup = lib.import_file(available.path)
    # settings in real units; last-run readings (hot pressure, update count) left out
    assert setup.values == {"Tires_LeftFront_StartingPressure": "170 kPa", "Chassis_Front_ArbSetting": "2",
                            "Chassis_Front_ToeIn": "-1.0 mm"}
    assert lib.import_file(available.path).id == setup.id  # same setup again: no new version
    assert lib.available()[0].in_library
    assert (group_of("Tires_LeftFront_StartingPressure"), pretty("Tires_LeftFront_StartingPressure")) == \
        ("Tires", "Starting pressure FL")
    assert pretty("Chassis_Front_ArbSetting") == "Front · Arb setting"
    with pytest.raises(ValueError):
        lib.import_file("iracing:no such run")


def test_steering_understeer_learns_the_cars_steering_from_gentle_corners():
    from fs_telemetry.maths import steering_understeer

    K, v = 40.0, 20.0  # steering ratio x wheelbase (m); 72 kph
    gentle_r, hard_r = 0.147, 0.4  # rad/s: about 0.3 G and 0.8 G at 20 m/s
    needed = lambda r: K * np.degrees(r / v)  # noqa: E731 - steering a car with no tyre slip would need
    # 200 samples of gentle cornering at exactly K, then: understeer left, understeer right, countersteer
    yaw = np.r_[np.full(200, gentle_r), hard_r, -hard_r, hard_r]
    steer = np.r_[np.full(200, needed(gentle_r)), needed(hard_r) + 10, -needed(hard_r) - 10, -5.0]
    x = {"Ground Speed": np.full(203, v * 3.6), "Chassis Yaw Rate": np.degrees(yaw), "Steering Angle": steer,
         "CG Accel Lateral": -yaw * v / 9.81, "Brake Pos": np.zeros(203)}
    out = steering_understeer(x)
    assert out[:200] == pytest.approx(0, abs=1e-6)  # the gentle corners are the reference
    assert out[200] == pytest.approx(10) and out[201] == pytest.approx(10)  # 10 deg too much lock either way: understeer +
    assert out[202] < -40  # countersteering: oversteer -
    assert steering_understeer({k: a[150:] for k, a in x.items()}) is None  # too little gentle cornering to learn from


def test_understeer_uses_known_car_facts_first_and_learns_the_rest(monkeypatch):
    """No steering ratio: everything learned (Steering Understeer, steering-wheel degrees).
    Ratio in the iRacing setup: Understeer Angle at the wheels, the corner's needs learned.
    Ratio and wheelbase in cars.py: Understeer Angle from known numbers only, not an estimate."""
    from fs_telemetry import cars
    from fs_telemetry.ingest.motec_ld import Channel, LdSession

    v, n = 20.0, 400
    yaw = np.full(n, 0.147)  # gentle left-hander at 72 kph, about 0.3 G
    steer = np.full(n, 40.0 * np.degrees(0.147 / v))  # K = ratio x wheelbase = 40 m
    data = {"Ground Speed": np.full(n, v * 3.6), "Chassis Yaw Rate": np.degrees(yaw), "Steering Angle": steer,
            "CG Accel Lateral": -yaw * v / 9.81, "Brake Pos": np.zeros(n)}

    def run(car, details=None):
        chans = {k: Channel(k, "", 60, a.copy()) for k, a in data.items()}
        s = LdSession(path=None, driver="", car=car, track="", date="", time="", channels=chans,
                      details=details or {}, source="iracing")
        return add_maths_channels(s).channels

    learned = run("Unknown Car")
    assert "Understeer Angle" not in learned and learned["Steering Understeer"].approximate
    assert "learned" in learned["Steering Understeer"].basis

    from_setup = run("Street Stock", {"CarSetup_Chassis_Front_SteeringRatio": "12:1"})
    assert "Steering Understeer" not in from_setup  # replaced by the better one
    assert from_setup["Understeer Angle"].approximate
    assert "known steering ratio 12.0:1 (the car's iRacing setup)" in from_setup["Understeer Angle"].basis
    assert "learned" in from_setup["Understeer Angle"].basis

    monkeypatch.setitem(cars.CARS, "Table Car", cars.Car(steering_ratio=16.0, wheelbase_m=2.5))
    known = run("Table Car")["Understeer Angle"]
    assert not known.approximate and "learned" not in known.basis and "cars.py" in known.basis
    assert "Based on: known steering ratio 16.0:1 (cars.py)" in known.description


def test_parser_handles_lists_and_odd_characters():
    text = "A:\n x: 1\n L:\n - k: v\x85w\n   m: \n - k: 2\n y: \"q\"\nB: 3\n"
    assert parse_session_info(text) == {"A": {"x": "1", "L": [{"k": "v\x85w", "m": ""}, {"k": "2"}], "y": "q"}, "B": "3"}


def test_mixed_text_encodings():
    from fs_telemetry.ingest.ibt import _decode

    assert _decode("FlairName: Åland".encode("utf-8") + b"\nTrackDisplayName: Aut\xf3dromo") == \
        "FlairName: Åland\nTrackDisplayName: Autódromo"


# --- Real data ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def ibt_files():
    from fs_telemetry.config import IRACING_DIR

    files = sorted(IRACING_DIR.rglob("*.ibt")) if IRACING_DIR.exists() else []
    if not files:
        pytest.skip("no .ibt files on this machine")
    return files


def test_lap_times_match_iracing_own(ibt_files):
    """Each lap's time should match what iRacing posts in LapLastLapTime once the
    lap is done. A few won't: iRacing times an out lap from before the line (e.g.
    from a standing start), and doesn't always post a time at all."""
    checked = matched = 0
    for path in ibt_files[::4]:
        s = read_ibt(path)
        last = s.channels.get("LapLastLapTime")
        if last is None:
            continue
        posted = np.flatnonzero(np.diff(last.data) != 0) + 1
        for info in lap_infos(s):
            after = posted[last.time[posted] > info.start + info.lap_time - 0.05]
            if info.kind != "timed" or not len(after) or last.data[after[0]] <= 0:
                continue
            checked += 1
            matched += abs(info.lap_time - last.data[after[0]]) < 0.01
    assert checked > 20 and matched / checked > 0.9


def test_api_works_on_an_iracing_session(ibt_files):
    from fs_telemetry.main import app

    client = TestClient(app)
    s = next(x for x in client.get("/api/sessions").json() if x["source"] == "iracing" and x["best_lap_number"])
    lap = f"{s['id']}:{s['best_lap_number']}"
    data = client.get(f"/api/sessions/{s['id']}/laps/{s['best_lap_number']}/data",
                      params={"channels": ["Ground Speed", "Throttle Pos", "Rear Wheelspin"]})
    assert data.status_code == 200 and len(data.json()["data"]["Ground Speed"]) > 100
    stint = client.get(f"/api/sessions/{s['id']}/stint").json()
    assert stint["summary"]["fuel_per_lap"] > 0 and len(stint["summary"]["best_splits_s"]) >= 2
    metrics = client.get("/api/metrics", params={"lap": [lap]}).json()
    # no slip angles in iRacing: balance comes from steering instead, and says so
    assert metrics["balance_label"].startswith("steering") and metrics["laps"][0]["summary"]["balance_deg"] is not None
    assert "learned" in metrics["laps"][0]["balance_basis"] and metrics["balance_axis"] == "Extra steering [°]"
    compare = client.get("/api/compare", params={"lap": [lap], "sectors": 10}).json()
    assert compare["outline"] is None and "iRacing" in compare["outline_note"] and len(compare["lines"][0]["x"]) > 100
    assert compare["sector_source"].startswith("iRacing")
