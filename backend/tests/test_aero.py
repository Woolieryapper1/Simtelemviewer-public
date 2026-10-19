"""Aero load tests: a made-up car with known weight and downforce, then real data."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fs_telemetry.aero import FREQ, aero_arrays, aero_map, calibrate
from fs_telemetry.ingest.motec_ld import Channel, LdSession

W, FRONT, K, CLA, CLA_FRONT, RHO = 3000.0, 0.45, -500.0, 3.8, 1.3, 1.2  # N, share, N/G, m², m², kg/m³


def fake_car(garbage: bool = False, sweep_s: float = 10, seconds: float = 180, noise_n: float = 20) -> LdSession:
    """3 minutes of driving: speed sweeps 0-35 m/s, braking/accelerating, bumps.
    Tyre loads follow the equations in app/aero.py exactly (plus noise)."""
    rng = np.random.default_rng(1)
    t = np.arange(0, seconds, 1 / FREQ)
    v = 17.5 - 17.5 * np.cos(t / sweep_s)  # m/s
    ax = np.gradient(v, t) / 9.81  # G
    az = 0.05 * np.sin(t * 7) + rng.normal(0, 0.01, len(t))
    q = 0.5 * RHO * v**2
    total = W * (1 + az) + CLA * q + rng.normal(0, noise_n, len(t))
    front = W * FRONT * (1 + az) + K * ax + CLA_FRONT * q + rng.normal(0, 10, len(t))
    if garbage:  # a 10 s "off": loads all over the place
        bad = (t > 60) & (t < 70)
        total[bad] *= rng.uniform(0.2, 2.5, bad.sum())
        front[bad] *= rng.uniform(0.2, 2.5, bad.sum())
    rear = total - front
    ch = lambda n, u, d: Channel(n, u, FREQ, np.asarray(d, float))
    channels = {
        "Tire Load FL": ch("Tire Load FL", "N", front / 2), "Tire Load FR": ch("Tire Load FR", "N", front / 2),
        "Tire Load RL": ch("Tire Load RL", "N", rear / 2), "Tire Load RR": ch("Tire Load RR", "N", rear / 2),
        "CG Accel Vertical": ch("CG Accel Vertical", "G", az), "CG Accel Longitudinal": ch("CG Accel Longitudinal", "G", ax),
        "CG Accel Lateral": ch("CG Accel Lateral", "G", np.zeros_like(t)),
        "Ground Speed": ch("Ground Speed", "kph", v * 3.6), "Air Density": ch("Air Density", "kg/m3", np.full_like(t, RHO)),
    }
    return LdSession(path=None, driver="", car="", track="", date="", time="", channels=channels,
                     details={"Vehicle Wheelbase": "1.5"})


@pytest.mark.parametrize("garbage", [False, True])
def test_calibration_recovers_the_car(garbage):
    cal, k = calibrate(fake_car(garbage))
    assert cal.weight_n == pytest.approx(W, rel=0.01)
    assert cal.static_front_pct == pytest.approx(100 * FRONT, abs=0.5)
    assert cal.coefficient_m2 == pytest.approx(CLA, rel=0.02)
    assert cal.balance_front_pct == pytest.approx(100 * CLA_FRONT / CLA, abs=1.0)
    assert k == pytest.approx(K, rel=0.05)
    assert cal.cg_height_m == pytest.approx(-K * 1.5 / W, rel=0.05)  # h = -k L / W
    assert cal.trustworthy


def test_error_bars_cover_the_real_error():
    cal, _ = calibrate(fake_car())
    assert 0 < cal.cla_error_pct < 5 and 0 < cal.balance_error_pp < 5
    assert 100 * abs(cal.coefficient_m2 - CLA) / CLA <= cal.cla_error_pct
    assert abs(cal.balance_front_pct - 100 * CLA_FRONT / CLA) <= cal.balance_error_pp


def test_short_and_noisy_runs_get_wider_bars_and_are_flagged():
    good, _ = calibrate(fake_car())
    short, _ = calibrate(fake_car(seconds=25, noise_n=250))  # ~25 s of running, noisy loads
    assert short.cla_error_pct > 3 * good.cla_error_pct
    assert not short.trustworthy and "TOO LITTLE" in short.note
    assert good.trustworthy


def test_too_few_straight_samples_gives_no_fit():
    assert calibrate(fake_car(seconds=9)) is None  # under 300 samples (10 s at 30 Hz)


def test_channel_descriptions_carry_the_error_bars():
    from fs_telemetry.aero import add_aero_channels
    s = fake_car()
    add_aero_channels(s)
    desc = s.channels["Aero Balance (est.)"].description
    assert "Error bars for this run: ClA ±" in desc and "points" in desc


def test_per_sample_downforce_and_balance():
    s = fake_car()
    _, a = aero_arrays(s)
    v = s.channels["Ground Speed"].data / 3.6
    fast = v > 30
    expected = CLA * 0.5 * RHO * v[fast] ** 2
    assert np.median(a["Aero Downforce"][fast] / expected) == pytest.approx(1, abs=0.03)
    assert np.median(a["Aero Balance"][fast]) == pytest.approx(100 * CLA_FRONT / CLA, abs=1.5)
    assert np.all(a["Aero Balance"][v < 10] == 0)  # too slow to split: reported as 0, never NaN


def test_aero_map_bands():
    m = aero_map(fake_car(sweep_s=20))  # gentler speed changes -> more "steady running" to band
    assert m.downforce_at_100kph_n == pytest.approx(0.5 * 1.225 * (100 / 3.6) ** 2 * CLA, rel=0.02)
    cla = [b.coefficient_m2 for b in m.bands]
    assert len(cla) >= 3 and max(cla) - min(cla) < 0.3  # same coefficient at every speed


def test_missing_channels_means_no_aero():
    s = fake_car()
    del s.channels["Tire Load RR"]
    assert calibrate(s) is None and aero_arrays(s) is None


# --- Real data ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def real():
    from fs_telemetry.config import TELEMETRY_DIR
    from fs_telemetry.main import app, catalog

    if not (TELEMETRY_DIR.exists() and any(TELEMETRY_DIR.rglob("*.ld"))):
        pytest.skip("no .ld files on this machine")
    return TestClient(app), catalog


def test_weight_matches_the_ldx_and_cla_is_consistent_per_car(real):
    _, catalog = real
    by_car: dict[str, list[float]] = {}
    for summary in [x for x in catalog.summaries() if x.source == "ac"]:  # iRacing doesn't log tyre loads
        s = catalog.get(summary.id)
        cal, _ = calibrate(s)
        assert cal.trustworthy, summary.id
        ldx_kg = float(s.details["Vehicle Weight"])
        assert cal.mass_kg == pytest.approx(ldx_kg, rel=0.03), summary.id  # within 3% of the car's listed weight
        by_car.setdefault(s.car, []).append(cal.coefficient_m2)
    for car, cla in by_car.items():
        assert np.std(cla) / np.mean(cla) < 0.06, car  # same car -> same ClA, run to run


def test_aero_endpoint_and_channels(real):
    client, _ = real
    s = next(x for x in client.get("/api/sessions").json() if x["car"].endswith("_hy"))
    m = client.get(f"/api/sessions/{s['id']}/aero").json()
    assert 3.5 < m["calibration"]["coefficient_m2"] < 4.2 and m["bands"]
    chans = {c["name"]: c for c in client.get(f"/api/sessions/{s['id']}/channels").json()}
    for name in ("Aero Downforce", "Aero Downforce Front", "Aero Downforce Rear", "Aero Balance", "Downforce Coefficient"):
        c = chans[f"{name} (est.)"]  # labelled as an estimate in the name...
        assert c["maths"] and c["approximate"] and c["description"].startswith("Approximation")  # ...and flagged
    assert not chans["G Absolute"]["approximate"]  # exact maths channels aren't labelled
    assert m["approximate"] is True and "Approximation" in m["note"]


def test_zero_longitudinal_acceleration_cannot_crash_session_loading():
    s = fake_car()
    s.channels["CG Accel Longitudinal"].data[:] = 0
    assert calibrate(s) is None
    from fs_telemetry.aero import add_aero_channels
    add_aero_channels(s)
    assert "Aero Balance (est.)" not in s.channels


def test_exact_fit_preserves_finite_uncertainty():
    from fs_telemetry.aero import _robust_fit
    x = np.arange(100, dtype=float)
    coef, residual, se = _robust_fit(np.c_[np.ones(100), x], 5 + 3 * x)
    assert coef == pytest.approx([5, 3])
    assert len(residual) > 2 and np.all(np.isfinite(se))
