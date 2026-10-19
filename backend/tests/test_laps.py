"""Tests for lap splitting and distance resampling, using a made-up session so
they run anywhere (no telemetry files needed)."""

import numpy as np

from fs_telemetry.ingest.motec_ld import Channel, LdSession
from fs_telemetry.laps import best_lap, lap_infos, resample_lap

L = 1000.0  # fake track length, m
FREQ = 20


def fake_session() -> LdSession:
    """Car starts in the pit lane at 900 m, then does laps at a constant 50 m/s
    (20 s per lap). Beacons at 2 s, 22 s, 42 s; session ends at 50 s."""
    t = np.arange(0, 50, 1 / FREQ)
    dist = (900 + 50 * t) % L
    # Mimic AC: the reset to 0 is logged one sample late.
    for b in (2.0, 22.0, 42.0):
        i = int(round(b * FREQ))
        dist[i] = L - 0.1
    gear = np.where(t % 20 < 10, 3.0, 4.0)
    invalid = np.zeros(50)  # 1 Hz
    invalid[30:35] = 1  # a track-limits cut during lap 3 (22..42 s)
    return LdSession(
        path=None, driver="Test", car="car", track="track", date="", time="",
        channels={
            "Lap Distance": Channel("Lap Distance", "m", FREQ, dist),
            "Ground Speed": Channel("Ground Speed", "kph", FREQ, np.full_like(t, 180.0)),
            "Gear": Channel("Gear", "", FREQ, gear),
            "Lap Invalidated": Channel("Lap Invalidated", "", 1, invalid),
        },
        beacons=[2.0, 22.0, 42.0],
    )


def test_lap_kinds_and_invalid():
    infos = lap_infos(fake_session())
    assert [l.kind for l in infos] == ["out", "timed", "timed", "incomplete"]
    assert [l.invalid for l in infos] == [False, False, True, False]
    assert best_lap(infos).number == 2


def test_resample_flying_lap_covers_track_on_distance_grid():
    out = resample_lap(fake_session(), 2, ["Ground Speed", "Gear"], step_m=10)
    assert out["Distance"][0] == 0 and out["Distance"][-1] >= L - 20
    assert np.all(np.diff(out["Distance"]) == 10)
    # 50 m/s -> 10 m takes 0.2 s, and time should increase smoothly from 0
    np.testing.assert_allclose(np.diff(out["Time"]), 0.2, atol=0.06)
    np.testing.assert_allclose(out["Ground Speed"], 180)


def test_discrete_channels_are_not_blended():
    out = resample_lap(fake_session(), 2, ["Gear"], step_m=1)
    assert set(np.unique(out["Gear"])) <= {3.0, 4.0}


def test_stale_samples_are_dropped_using_ac_lap_timer():
    s = fake_session()
    t = s.channels["Lap Distance"].time
    lap_time = t - np.select([t >= 42, t >= 22, t >= 2], [42.0, 22.0, 2.0], 0.0)
    # Mimic the logger repeating an old sample: distance AND lap time step back.
    for i in range(500, 800, 37):
        s.channels["Lap Distance"].data[i] = s.channels["Lap Distance"].data[i - 2]
        lap_time[i] = lap_time[i - 2]
    s.channels["Lap Time"] = Channel("Lap Time", "s", FREQ, lap_time)

    out = resample_lap(s, 2, [], step_m=10)
    # Constant 50 m/s: every 10 m takes exactly 0.2 s - no glitches survive.
    np.testing.assert_allclose(np.diff(out["Time"]), 0.2, atol=1e-6)


def test_distance_reset_logged_before_a_stale_sample_at_the_line():
    """Seen in real data: at the end of a lap the logger wrote the next lap's
    distance (0.8 m), then a stale sample back at the end of the track."""
    s = fake_session()
    t = s.channels["Lap Distance"].time
    lap_time = t - np.select([t >= 42, t >= 22, t >= 2], [42.0, 22.0, 2.0], 0.0)
    s.channels["Lap Time"] = Channel("Lap Time", "s", FREQ, lap_time)
    d = s.channels["Lap Distance"].data
    i = int(round(42.0 * FREQ))  # last samples of lap 3, just before the beacon
    d[i - 2], lap_time[i - 2] = 0.8, lap_time[i - 2]  # next lap's distance, this lap's time
    d[i - 1], lap_time[i - 1] = d[i - 3], lap_time[i - 3]  # stale repeat
    out = resample_lap(s, 3, [], step_m=10)
    assert out["Distance"][0] < 20 and out["Distance"][-1] > L - 20  # still a whole lap


def test_out_lap_only_keeps_the_part_after_the_line():
    out = resample_lap(fake_session(), 1, ["Ground Speed"], step_m=10)
    # Out lap is 900 m -> line (100 m), anchored to finish at L
    assert out["Distance"][0] >= 890 and out["Distance"][-1] <= L
