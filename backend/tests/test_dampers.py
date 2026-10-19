"""Damper histogram tests: made-up suspension travel with a known velocity
distribution, so bin counts, band percentages and signs can be checked exactly."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fs_telemetry import maths
from fs_telemetry.compare import CompareError, LapRef
from fs_telemetry.dampers import (BANDS, SIGNALS, DamperSettings, band_index, compute_dampers, damper_stats,
                                  histogram, histogram_edges, signals)
from fs_telemetry.ingest.motec_ld import Channel, LdSession
from fs_telemetry.metrics import MetricSettings

L = 1000.0  # track length, m
SPEED = 50.0  # m/s, constant -> a lap takes 20 s
DIST_HZ, TRAVEL_HZ = 20, 30
CORNERS = ("FL", "FR", "RL", "RR")


def triangle_travel(t, up_mm_s, up_s, down_mm_s):
    """Travel that rises at up_mm_s for up_s seconds, then falls at down_mm_s until back where it started.
    So the damper velocity is +up_mm_s (bump) for up_s, then -down_mm_s (rebound) for the rest of each cycle."""
    rise = up_mm_s * up_s
    period = up_s + rise / down_mm_s
    phase = np.mod(t, period)
    return 30.0 + np.where(phase < up_s, up_mm_s * phase, rise - down_mm_s * (phase - up_s))


def fake_session(travel_of_t=None, brake_until_m=0.0, laps=4, logged_velocity=None, corners=CORNERS) -> LdSession:
    """A run at constant speed: out lap from 900 m, then `laps` laps of 1000 m. Brake pressed over the
    first `brake_until_m` metres of every lap. travel_of_t(t) -> mm for every corner (or a dict per corner)."""
    duration = (L - 900.0 + laps * L) / SPEED + 1.0
    t = np.arange(int(duration * DIST_HZ)) / DIST_HZ
    total = 900.0 + SPEED * t
    d = np.mod(total, L)
    beacons = [k * L / SPEED - 900.0 / SPEED for k in range(1, laps + 2)]  # session time at each line crossing
    beacons = [b for b in beacons if b < t[-1]]
    last = np.array([max([0.0] + [b for b in beacons if b <= x]) for x in t])
    ch = lambda name, unit, data, hz=DIST_HZ: Channel(name, unit, hz, np.asarray(data, float))
    chans = {
        "Lap Distance": ch("Lap Distance", "m", d),
        "Lap Time": ch("Lap Time", "s", t - last),
        "Ground Speed": ch("Ground Speed", "kph", np.full_like(t, SPEED * 3.6)),
        "Throttle Pos": ch("Throttle Pos", "%", np.where(d < brake_until_m, 0.0, 60.0)),
        "Brake Pos": ch("Brake Pos", "%", np.where(d < brake_until_m, 80.0, 0.0)),
        "CG Accel Lateral": ch("CG Accel Lateral", "G", np.full_like(t, 1.5)),
        "CG Accel Longitudinal": ch("CG Accel Longitudinal", "G", np.zeros_like(t)),
    }
    tt = np.arange(int(duration * TRAVEL_HZ)) / TRAVEL_HZ
    travel_of_t = travel_of_t or (lambda x: np.full_like(x, 30.0))
    for c in corners:
        f = travel_of_t[c] if isinstance(travel_of_t, dict) else travel_of_t
        chans[f"Suspension Travel {c}"] = ch(f"Suspension Travel {c}", "mm", f(tt), TRAVEL_HZ)
        if logged_velocity is not None:
            chans[f"Damper Velocity {c}"] = ch(f"Damper Velocity {c}", "mm/s", logged_velocity(tt), TRAVEL_HZ)
    s = LdSession(path=None, driver="Test", car="car", track="track", date="", time="", channels=chans, beacons=beacons)
    return maths.add_maths_channels(s)


class FakeCatalog:
    def __init__(self, **sessions):
        self.sessions = sessions

    def get(self, session_id):
        return self.sessions.get(session_id)


def run(cat, *refs, **settings):
    gating = {k: settings.pop(k) for k in list(settings) if k in MetricSettings.model_fields}
    return compute_dampers(cat, [LapRef.parse(r) for r in refs], DamperSettings(**settings), MetricSettings(**gating))


def hist(sel, signal):
    return sel.histograms[SIGNALS.index(signal)]


# --- The maths on plain arrays -------------------------------------------------------


def test_band_split_and_sign_convention():
    v = np.array([-150, -50, -10, 0, 10, 50, 150, 150], dtype=float)
    assert [BANDS[i] for i in band_index(v, 25, 100)] == [
        "Rebound high", "Rebound mid", "Rebound low", "Bump low", "Bump low", "Bump mid", "Bump high", "Bump high"]
    st = damper_stats(v, 25, 100)
    assert st.bands_pct == [12.5, 12.5, 12.5, 25.0, 12.5, 25.0]
    assert st.mean_bump_mm_s == pytest.approx((10 + 50 + 150 + 150) / 4, abs=0.1)
    assert st.mean_rebound_mm_s == pytest.approx(-70.0, abs=0.1)  # rebound is negative
    assert (st.peak_bump_mm_s, st.peak_rebound_mm_s) == (150.0, -150.0)
    assert st.bump_rebound_ratio == pytest.approx(4 / 3, abs=0.001)  # the 0 counts as neither


def test_thresholds_are_settings():
    v = np.array([-60, -30, 30, 60], dtype=float)
    assert damper_stats(v, 25, 100).bands_pct == [0, 50, 0, 0, 50, 0]
    assert damper_stats(v, 40, 50).bands_pct == [25, 0, 25, 25, 0, 25]


def test_no_samples_gives_empty_stats():
    st = damper_stats(np.array([]), 25, 100)
    assert st.bands_pct == [0.0] * 6 and st.mean_bump_mm_s is None and st.bump_rebound_ratio is None


def test_bins_are_symmetric_with_an_edge_at_zero_and_end_bins_catch_the_rest():
    v = np.array([-1000.0, -15, -5, 5, 5, 15, 25, 1000.0])
    edges = histogram_edges([np.linspace(-120, 120, 1000)], 10)
    assert edges[0] == -edges[-1] and 0.0 in edges and edges[-1] == 150  # 99.5th pct ~119 -> rounded up to 150
    counts = histogram(v, edges)
    assert counts.sum() == len(v)  # nothing dropped: the far-out samples are in the end bins
    zero = list(edges).index(0.0)
    assert counts[0] == 1 and counts[-1] == 1
    assert counts[zero - 2:zero + 3].tolist() == [1, 1, 2, 1, 1]  # -15, -5, (5, 5), 15, 25


def test_heave_and_roll():
    corners = {"FL": np.array([100.0]), "FR": np.array([-100.0]), "RL": np.array([20.0]), "RR": np.array([40.0])}
    s = signals(corners)
    assert s["Front heave"][0] == 0 and s["Front roll"][0] == 200  # + roll: left compressing faster than right
    assert s["Rear heave"][0] == 30 and s["Rear roll"][0] == -20
    assert sorted(s["Front"]) == [-100, 100] and sorted(s["Left"]) == [20, 100]


# --- Derived damper velocity (AC) ------------------------------------------------------


def test_derived_velocity_from_travel_sign_and_size():
    t = np.arange(300) / 30
    # 20 mm amplitude at 0.5 Hz: peak speed 2*pi*0.5*20 = 62.8 mm/s. Slow, so 30 Hz catches it well.
    x = {"Suspension Travel FL": 30 + 20 * np.sin(2 * np.pi * 0.5 * t)}
    v = maths.damper_velocity("FL", 30, 1.0)(x)
    np.testing.assert_allclose(v[1:-1], 62.83 * np.cos(2 * np.pi * 0.5 * t[1:-1]), atol=0.5)
    assert v[1] > 0  # travel rising (compressing) = bump = +
    # Travel in metres is converted to mm
    vm = maths.damper_velocity("FL", 30, 1000.0)({"Suspension Travel FL": x["Suspension Travel FL"] / 1000})
    np.testing.assert_allclose(vm, v)


def test_estimated_channel_is_added_for_ac_and_marked_approximate():
    s = fake_session(lambda x: 30 + 0.04 * np.sin(x))
    ch = s.channels["Damper Velocity FL (est.)"]
    assert ch.maths and ch.approximate and ch.unit == "mm/s"
    assert "30 Hz" in ch.description and "under-read" in ch.description


def test_logged_damper_velocity_is_kept_and_no_estimate_added():
    s = fake_session(logged_velocity=lambda x: np.full_like(x, 7.0))
    assert "Damper Velocity FL (est.)" not in s.channels


# --- Whole pipeline: known distributions -------------------------------------------------


def test_known_distribution_end_to_end():
    # +125 mm/s for 0.5 s, then -45 mm/s for 1.39 s: 26.5% fast bump, 73.5% mid rebound
    # (speeds away from the 10 mm/s bar edges, so each lands squarely in one bar)
    cat = FakeCatalog(A=fake_session(lambda x: triangle_travel(x, 125, 0.5, 45)))
    d = run(cat, "A:2")
    sel = d.selections[0]
    assert sel.estimated and "30 Hz" in sel.source and "estimated" in d.note
    assert sel.time_s == pytest.approx(20.0, abs=0.1)  # one 20 s lap
    st = hist(sel, "FL").stats
    # The two samples at each turn blend bump and rebound, so allow a few %
    b = dict(zip(BANDS, st.bands_pct))
    assert b["Bump high"] == pytest.approx(26.5, abs=4) and b["Rebound mid"] == pytest.approx(73.5, abs=4)
    assert st.bump_rebound_ratio == pytest.approx(0.36, abs=0.05)
    assert st.mean_rebound_mm_s == pytest.approx(-45, abs=3)
    # Bin counts: almost every sample in the bar holding +125 or the bar holding -45
    edges = np.array(d.edges)
    h = hist(sel, "FL")
    at = lambda v: int(np.searchsorted(edges, v, side="right") - 1)
    assert h.counts[at(125)] + h.counts[at(-45)] >= 0.93 * sum(h.counts)
    assert sum(h.pct) == pytest.approx(100, abs=0.1)
    # Same travel at every corner: heave matches the corners, roll is all zero
    assert hist(sel, "Front roll").stats.bands_pct[3] == 100.0  # 0 counts as "bump low"
    assert hist(sel, "Front heave").stats.bands_pct == st.bands_pct


def test_bump_only_corner_is_all_positive():
    travel = {c: (lambda x: 30 + 0 * x) for c in CORNERS}
    travel["FL"] = lambda x: 30 + 60 * x  # steadily compressing at 60 mm/s
    d = run(FakeCatalog(A=fake_session(travel)), "A:2")
    st = hist(d.selections[0], "FL").stats
    assert st.bands_pct[4] == 100.0 and st.mean_rebound_mm_s is None and st.mean_bump_mm_s == pytest.approx(60)
    assert hist(d.selections[0], "Front roll").stats.mean_bump_mm_s == pytest.approx(60)  # left compressing faster


def test_gate_only_counts_that_part_of_the_lap():
    cat = FakeCatalog(A=fake_session(brake_until_m=300.0))  # braking over 30% of a constant-speed lap
    assert run(cat, "A:2", gate="Braking").selections[0].time_s == pytest.approx(6.0, abs=0.2)
    assert run(cat, "A:2", gate="LS").selections[0].time_s == 0  # no slow corners at 180 kph
    assert run(cat, "A:2", gate="HS").selections[0].time_s == pytest.approx(14.0, abs=0.2)


def test_whole_run_and_compare_two_runs():
    a = fake_session(lambda x: triangle_travel(x, 120, 0.5, 40), laps=4)
    b = fake_session(lambda x: triangle_travel(x, 40, 1.0, 40), laps=3)
    d = run(FakeCatalog(A=a, B=b), "A:3", "A:2", "B:2", scope="run")
    assert [s.session_id for s in d.selections] == ["A", "B"]  # one per run, in compared order
    assert d.selections[0].lap is None and d.selections[0].laps == [2, 3, 4, 5]  # timed laps only, not out/in lap
    assert d.selections[0].time_s == pytest.approx(80, abs=0.3)
    # Both runs share the same bins, so their bars line up
    assert len(hist(d.selections[0], "FL").counts) == len(hist(d.selections[1], "FL").counts) == len(d.edges) - 1
    assert hist(d.selections[1], "FL").stats.bump_rebound_ratio == pytest.approx(1.0, abs=0.05)


def test_missing_channels_are_named():
    s = fake_session(corners=("FL", "FR", "RL"))
    with pytest.raises(CompareError, match="Suspension Travel RR"):
        run(FakeCatalog(A=s), "A:2")


def test_settings_validation():
    with pytest.raises(ValueError):
        DamperSettings(low_mm_s=100, high_mm_s=50)


# --- Real data, when there is some on this machine -------------------------------------


def test_real_run_through_the_api():
    from fs_telemetry.config import IRACING_DIR, TELEMETRY_DIR
    from fs_telemetry.main import app

    has = lambda root, pat: root and root.exists() and any(root.rglob(pat))
    if not (has(TELEMETRY_DIR, "*.ld") or has(IRACING_DIR, "*.ibt")):
        pytest.skip("no telemetry on this machine")
    client = TestClient(app)
    s = next(s for s in client.get("/api/sessions").json() if s["best_lap_number"])
    r = client.get("/api/dampers", params={"lap": f"{s['id']}:{s['best_lap_number']}", "gate": "Braking"})
    if r.status_code == 422:
        assert "damper velocity" in r.json()["detail"]  # a file without suspension travel says so
        return
    d = r.json()
    for h in d["selections"][0]["histograms"]:
        assert sum(h["stats"]["bands_pct"]) == pytest.approx(100, abs=0.1) or d["selections"][0]["time_s"] == 0
