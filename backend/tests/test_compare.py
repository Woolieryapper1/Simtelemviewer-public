"""Track dominance tests: made-up laps where we know the right answer, plus a
real-data check if exports are available."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fs_telemetry.compare import CompareError, LapRef, compare_laps
from fs_telemetry.ingest.motec_ld import Channel, LdSession

L = 1000.0
FREQ = 20


def fake_lap_session(speed_first_half: float, speed_second_half: float) -> LdSession:
    """Out lap from 900 m, then laps at the given speeds (m/s) in each half.
    Like AC, the lap timer resets to exactly 0 on the beacon sample."""
    dt = 1 / FREQ
    t, d, lt, beacons = [0.0], [900.0], [0.0], []
    while len(t) < 2000:
        v = 50.0 if not beacons else (speed_first_half if d[-1] < L / 2 else speed_second_half)
        nxt = d[-1] + v * dt
        t.append(t[-1] + dt)
        if nxt >= L:  # crossed the line
            nxt -= L
            beacons.append(t[-1])
        d.append(nxt)
        lt.append(t[-1] - (beacons[-1] if beacons else 0.0))
    t, d, lt = np.array(t), np.array(d), np.array(lt)
    theta = d / L * 2 * np.pi
    ch = lambda name, unit, data: Channel(name, unit, FREQ, np.asarray(data, float))
    return LdSession(
        path=None, driver=f"{speed_first_half}/{speed_second_half}", car="car", track="track", date="", time="",
        channels={
            "Lap Distance": ch("Lap Distance", "m", d),
            "Lap Time": ch("Lap Time", "s", lt),
            "Car Coord X": ch("Car Coord X", "m", 150 * np.cos(theta)),
            "Car Coord Y": ch("Car Coord Y", "m", 150 * np.sin(theta)),
            "Ground Speed": ch("Ground Speed", "kph", np.full_like(d, 100.0)),
            "Lap Invalidated": Channel("Lap Invalidated", "", 1, np.zeros(int(t[-1]) + 1)),
        },
        beacons=beacons,
    )


class FakeCatalog:
    def __init__(self, **sessions):
        self.sessions = sessions

    def get(self, session_id):
        return self.sessions.get(session_id)

    def summaries(self):
        from types import SimpleNamespace
        return [SimpleNamespace(id=k, track=s.track) for k, s in self.sessions.items()]


def test_each_lap_wins_the_half_where_it_is_faster():
    # A is faster in the first half, B in the second; 10 mini-sectors of 100 m
    cat = FakeCatalog(A=fake_lap_session(50, 40), B=fake_lap_session(40, 50))
    c = compare_laps(cat, [LapRef("A", 2), LapRef("B", 2)], n_sectors=10)
    winners = [s.winner for s in c.sectors]
    assert winners[:5] == [0] * 5 and winners[5:] == [1] * 5
    # 100 m at 50 m/s = 2.0 s vs 2.5 s at 40 m/s -> 0.5 s margin each
    np.testing.assert_allclose([s.margin_s for s in c.sectors], 0.5, atol=0.06)
    # Theoretical best = all sectors at 50 m/s = 20 s
    assert c.theoretical_best_s == pytest.approx(20.0, abs=0.1)
    # Mini-sector times add up to the lap time
    for lap in c.laps:
        assert sum(lap.sector_times_s) == pytest.approx(lap.lap_time_s, abs=0.01)
    assert [l.sectors_won for l in c.laps] == [5, 5]
    # No AC sector timing in the fake data -> equal thirds; splits add up to the lap
    assert c.sector_source.startswith("equal thirds") and len(c.sector_lines_m) == 2
    for lap in c.laps:
        assert sum(lap.splits_s) == pytest.approx(lap.lap_time_s, abs=0.01)
    # A is quicker in S1 (all first half), B in S3 (all second half)
    assert c.best_splits_s[0] == c.laps[0].splits_s[0] and c.best_splits_s[2] == c.laps[1].splits_s[2]
    assert c.theoretical_best_sectors_s < min(l.lap_time_s for l in c.laps)


def test_out_laps_and_mixed_tracks_are_refused():
    a, b = fake_lap_session(50, 50), fake_lap_session(50, 50)
    b.track = "elsewhere"
    cat = FakeCatalog(A=a, B=b)
    with pytest.raises(CompareError, match="out lap"):
        compare_laps(cat, [LapRef("A", 1)], 10)
    with pytest.raises(CompareError, match="different tracks"):
        compare_laps(cat, [LapRef("A", 2), LapRef("B", 2)], 10)


def test_lap_ref_parsing():
    assert LapRef.parse("25112025-011313-Driver-car-track:4") == LapRef("25112025-011313-Driver-car-track", 4)
    with pytest.raises(CompareError):
        LapRef.parse("no-lap-number")


def test_compare_api_on_real_data():
    from fs_telemetry.config import TELEMETRY_DIR
    from fs_telemetry.main import app

    if not (TELEMETRY_DIR.exists() and any(TELEMETRY_DIR.rglob("*.ld"))):
        pytest.skip("no .ld files on this machine")
    client = TestClient(app)
    sessions = [s for s in client.get("/api/sessions").json() if s["best_lap_number"]]
    # two best laps from the same track
    track = sessions[0]["track"]
    pair = [s for s in sessions if s["track"] == track][:2]
    laps = [f"{s['id']}:{s['best_lap_number']}" for s in pair]
    c = client.get("/api/compare", params={"lap": laps, "sectors": 25}).json()
    assert len(c["sectors"]) == 25 and len(c["map"]["x"]) > 100
    for line in c["lines"]:  # pedals come with every racing line point, for pedal colouring
        assert len(line["throttle"]) == len(line["brake"]) == len(line["x"])
        assert 0 <= min(line["throttle"]) and max(line["brake"]) <= 100
    assert c["theoretical_best_s"] <= min(l["lap_time_s"] for l in c["laps"]) + 1e-6
    csv = client.get("/api/compare.csv", params={"lap": laps}).text.splitlines()
    assert csv[0].startswith("Mini-sector,Start [m],End [m]") and len(csv) == 1 + 25 + 2
    assert client.get("/api/compare", params={"lap": "nope:1"}).status_code == 422


def test_sector_lines_match_ac_own_sector_times_on_real_data():
    """Our split of every lap at the found sector lines must agree with the
    sector times AC itself logged (within the 1 Hz logging resolution)."""
    from fs_telemetry.config import TELEMETRY_DIR
    from fs_telemetry.laps import lap_infos, resample_lap
    from fs_telemetry.main import catalog
    from fs_telemetry.sectors import sector_boundaries, split_times

    if not (TELEMETRY_DIR.exists() and any(TELEMETRY_DIR.rglob("*.ld"))):
        pytest.skip("no .ld files on this machine")
    track = next(x for x in catalog.summaries() if x.source == "ac").track
    lines, source = sector_boundaries(catalog, track)
    assert source.startswith("AC sector timing")
    errors = []
    for x in [x for x in catalog.summaries() if x.track == track]:
        s = catalog.get(x.id)
        ch = s.channels["Last Sector Time"]
        for info in lap_infos(s):
            if info.kind != "timed":
                continue
            lap = s.laps[info.number - 1]
            idx = np.flatnonzero((ch.time > lap.start + 1.5) & (ch.time < lap.end))
            v = ch.data[idx]
            ac = v[v != np.concatenate([[ch.data[idx[0] - 1]], v[:-1]])]
            if len(ac) == len(lines):
                ours = split_times(resample_lap(s, info.number, [], 2.0), lines, info.lap_time)
                errors += [abs(a - b) for a, b in zip(ours, ac)]
    assert errors and max(errors) < 0.05
