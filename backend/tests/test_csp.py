"""Tests for turning a CSP telemetry logger file into a telemetrick-like session,
using made-up sessions so they run anywhere (no telemetry files needed)."""

import numpy as np
import pytest
from test_ac_track import R, N, write_fake_ai

from fs_telemetry.ingest.csp import adapt_csp_log, is_csp_log
from fs_telemetry.ingest.motec_ld import Channel, LdSession
from fs_telemetry.laps import best_lap, lap_infos, resample_lap, track_length

L = 1000.0  # fake track length, m
FREQ = 50
SPEED = 50.0  # m/s -> 20 s a lap


def csp_session(length=L, duration=85.0, start=0.4, teleport=None, track="not_installed") -> LdSession:
    """Laps at a constant 50 m/s, named and scaled the way CSP's logger does it.
    The car starts at 40% of the lap, so with a 1000 m track it crosses the line
    at 12, 32, 52 and 72 s. One stray sample sits right on the crossing at 32 s,
    and CSP's own track-limits flag is set during the lap from 52 to 72 s.
    teleport=(time, lap share): jump back that far at that time."""
    t = np.arange(0, duration, 1 / FREQ)
    done = start + SPEED * t / length
    if teleport:
        done[t >= teleport[0]] -= teleport[1]
    progress = 100 * (done % 1.0)
    crossings = np.flatnonzero(np.floor(done[1:]) > np.floor(done[:-1]))
    if len(crossings) > 1:
        progress[crossings[1] + 1] = 29.12  # stray sample, e.g. 99.95, 29.12, 0.15 %
    angle = 2 * np.pi * done
    t2 = np.arange(0, duration, 0.5)
    flag = ((t2 >= 55) & (t2 < 72)).astype(float)
    ch = lambda name, unit, values, freq=FREQ: (name, Channel(name, unit, freq, np.asarray(values, float)))  # noqa: E731
    channels = dict([
        ch("Lap Progression", "%", progress),
        ch("Ground Speed", "m/s", np.full_like(t, SPEED)),
        ch("G Force Lat", "G", np.full_like(t, 0.5)),
        ch("Chassis Yaw Rate", "deg/s", np.full_like(t, 10.0)),
        ch("Chassis Angular Velocity Y", "deg/s", np.full_like(t, 10.0)),
        ch("Chassis World Pos X", "m", 100 * np.cos(angle)),
        ch("Chassis World Pos Y", "m", np.full_like(t, 3.0)),
        ch("Chassis World Pos Z", "m", 100 * np.sin(angle)),
        ch("Tire FX FL", "N", np.full_like(t, 1000.0)),
        ch("Tire Slip Ratio FL", "ratio", np.full_like(t, 0.05)),
        ch("Clutch Pos", "ratio", np.ones_like(t)),
        ch("Tire Temp Core FL", "°C", np.full_like(t, 80.0)),
        *(ch(f"Ride Height {c}", "mm", np.full_like(t, h)) for c, h in (("FL", 40), ("FR", 42), ("RL", 60), ("RR", 62))),
        ch("Lap Invalidated", "", flag, 2),
        ch("Time Phys", "s", 5 + t2, 2),
    ])
    return LdSession(path=None, driver="Test", car="car", track=track, date="", time="", channels=channels)


def test_laps_come_from_where_the_progression_wraps():
    s = csp_session()
    assert is_csp_log(s)
    adapt_csp_log(s, tracks_dirs=[])
    assert not is_csp_log(s)
    np.testing.assert_allclose(s.beacons, [12, 32, 52, 72], atol=0.002)  # the stray sample at 32 s didn't hide it
    infos = lap_infos(s)
    assert [l.kind for l in infos] == ["out", "timed", "timed", "timed", "incomplete"]
    assert [l.invalid for l in infos] == [False, False, False, True, False]  # CSP's flag, nothing else
    np.testing.assert_allclose([l.lap_time for l in infos[1:4]], 20, atol=0.002)
    assert best_lap(infos).number in (2, 3)
    assert track_length(s) == pytest.approx(L, abs=2)  # from the distance driven per lap
    assert s.details["Venue Length"] == "1000.0"


def test_lap_channels_line_up_with_the_line():
    s = adapt_csp_log(csp_session(), tracks_dirs=[])
    at = lambda name, t: s.channels[name].data[int(t * FREQ)]  # noqa: E731
    assert at("Lap Distance", 22) == pytest.approx(500, abs=1)
    assert at("Lap Number", 22) == 1 and at("Lap Number", 40) == 2
    assert at("Lap Time", 40) == pytest.approx(8, abs=0.01)
    assert at("Car Pos Norm", 22) == pytest.approx(0.5, abs=0.001)
    lap = resample_lap(s, 2, ["Ground Speed"], step_m=10)
    assert lap["Distance"][0] <= 10 and lap["Distance"][-1] >= L - 20  # a 10 m grid from the line
    assert lap["Time"][-1] == pytest.approx(20, abs=0.3)


def test_channels_get_telemetricks_names_units_and_signs():
    s = adapt_csp_log(csp_session(), tracks_dirs=[])
    c = s.channels
    for gone in ("G Force Lat", "Chassis World Pos X", "Tire FX FL", "Chassis Angular Velocity Y"):
        assert gone not in c
    assert (c["Ground Speed"].unit, c["Ground Speed"].data[0]) == ("kph", pytest.approx(180))
    assert c["CG Accel Lateral"].data[0] == 0.5  # same sign as telemetrick...
    assert c["Chassis Yaw Rate"].data[0] == -10  # ...but CSP's yaw rate is the other way round
    assert c["Car Coord X"].data[0] == pytest.approx(-c_world_x(s))
    assert c["Car Coord Y"].unit == "m" and c["Car Coord Z"].data[0] == 3
    assert c["Tire Long Fx FL"].data[0] == -1000
    assert (c["Tire Slip Ratio FL"].unit, c["Tire Slip Ratio FL"].data[0]) == ("%", pytest.approx(5))
    assert c["Clutch Pos"].data[0] == 100
    assert c["Tire Temp Core FL"].unit == "C"
    assert c["Ride Height Front"].data[0] == 41 and c["Ride Height Rear"].data[0] == 61


def c_world_x(s):
    """World X at the first sample of csp_session(): the car starts at 40% round the circle."""
    return 100 * np.cos(2 * np.pi * 0.4)


def test_a_crossing_just_out_of_the_pits_is_part_of_the_out_lap():
    # Pit box at 90%, just before the line: the crossing at 2 s comes straight out of the
    # pit lane, so the out lap runs on to the next one
    s = adapt_csp_log(csp_session(start=0.9), tracks_dirs=[])
    np.testing.assert_allclose(s.beacons[:2], [22, 42], atol=0.002)
    assert lap_infos(s)[0].kind == "out"


def test_a_jump_along_the_lap_spoils_only_its_own_lap():
    # The progression jumps back 10% at 40 s (mid-lap 3) while the car drives on: that lap can't be timed
    s = adapt_csp_log(csp_session(duration=95, teleport=(40.0, 0.1)), tracks_dirs=[])
    infos = lap_infos(s)
    assert infos[2].invalid and infos[2].lap_time == pytest.approx(22, abs=0.01)
    assert not infos[1].invalid
    assert s.breaks == []
    assert track_length(s) == pytest.approx(L, abs=2)  # the spoilt lap isn't used for the length


def test_back_to_the_pits_ends_the_lap_and_starts_an_out_lap():
    # At 40 s (mid-lap 3) the car is put back in the pits, 30% back along the lap, at rest for 2 s
    s = csp_session(duration=105, teleport=(40.0, 0.3))
    speed = s.channels["Ground Speed"]
    speed.data[(speed.time >= 40) & (speed.time < 42)] = 0
    adapt_csp_log(s, tracks_dirs=[])
    assert s.breaks == [pytest.approx(40, abs=0.03)]
    infos = lap_infos(s)
    assert [l.kind for l in infos[:6]] == ["out", "timed", "incomplete", "out", "timed", "timed"]
    assert infos[2].lap_time == pytest.approx(8, abs=0.03)  # 32 s to 40 s
    assert not infos[5].invalid  # a later full lap is fine (lap 5 has the track-limits flag)
    assert s.channels["Lap Time"].data[int(41 * FREQ)] == pytest.approx(1, abs=0.03)  # lap clock restarted
    for info in infos[2:4]:  # neither half resamples to a full lap, and isn't timed
        assert info.kind != "timed"


def test_an_installed_tracks_ai_line_gives_the_length(tmp_path):
    write_fake_ai(tmp_path / "trk" / "ai" / "fast_lane.ai")
    ai_line = 2 * np.pi * R * (N - 1) / N  # distance to the last point of the fake line
    # The driven line is 3% longer than the AI line, as the estimate can be
    s = adapt_csp_log(csp_session(length=ai_line * 1.03, duration=60, track="trk"), tracks_dirs=[tmp_path])
    assert track_length(s) == pytest.approx(ai_line, abs=1)
    assert "AI line" in s.channels["Lap Distance"].description


def test_no_progression_means_distance_driven():
    """E.g. a drift area: the track has no AI line, so the progression stays at 0."""
    s = csp_session(duration=10)
    s.channels["Lap Progression"].data[:] = 0
    adapt_csp_log(s, tracks_dirs=[])
    assert s.beacons == []
    assert s.channels["Lap Distance"].data[-1] == pytest.approx(SPEED * 10, abs=2)
    assert [l.kind for l in lap_infos(s)] == ["incomplete"]


def test_telemetrick_files_are_left_alone():
    s = csp_session()
    s.channels["Lap Distance"] = Channel("Lap Distance", "m", FREQ, np.zeros(10))
    assert not is_csp_log(s)
