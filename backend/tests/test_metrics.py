"""Corner gating, balance and metric-settings tests."""

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fs_telemetry.metrics import (BRAKING, HS, LS, MS, POWER, MetricSettings, balance, balance_stats, classify,
                                  corner_numbers, mech_stats, mech_vs_lat_g)


def gates(speed, throttle, brake, lat, **settings):
    a = lambda v: np.array(v, dtype=float)
    return list(classify(a(speed), a(throttle), a(brake), a(lat), MetricSettings(**settings)))


def test_braking_wins_over_everything():
    assert gates([50], [100], [30], [0.1]) == [BRAKING]


def test_power_limited_or_rule():
    # flat out in a corner -> power limited with "or"; part throttle on a straight too
    assert gates([100, 100], [100, 40], [0, 0], [1.5, 0.2]) == [POWER, POWER]


def test_power_limited_and_rule_leaves_flat_out_corners_in_speed_bands():
    # same samples with "and": only flat out AND straight is power limited
    assert gates([100, 100, 100], [100, 40, 100], [0, 0, 0], [1.5, 0.2, 0.2], power_rule="and") == [MS, MS, POWER]


def test_speed_bands_use_the_limits():
    cornering = dict(throttle=[50] * 3, brake=[0] * 3, lat=[1.5] * 3)
    assert gates([40, 120, 200], **cornering) == [LS, MS, HS]
    assert gates([40, 70, 120], **cornering, ls_max_kph=60, ms_max_kph=90) == [LS, MS, HS]


def test_balance_sign():
    fronts_slide = {"Tire Slip Angle FL": np.array([6.0]), "Tire Slip Angle FR": np.array([-6.0]),
                    "Tire Slip Angle RL": np.array([4.0]), "Tire Slip Angle RR": np.array([-4.0])}
    assert balance(fronts_slide)[0] == pytest.approx(2.0)  # + = understeer


def test_bands_must_be_in_order():
    with pytest.raises(ValidationError):
        MetricSettings(ls_max_kph=100, ms_max_kph=90)


def _real_laps():
    from fs_telemetry.config import TELEMETRY_DIR
    from fs_telemetry.main import app

    if not (TELEMETRY_DIR.exists() and any(TELEMETRY_DIR.rglob("*.ld"))):
        pytest.skip("no .ld files on this machine")
    client = TestClient(app)
    sessions = [s for s in client.get("/api/sessions").json() if s["best_lap_number"]]
    track = sessions[0]["track"]
    pair = [s for s in sessions if s["track"] == track][:2]
    return client, [f"{s['id']}:{s['best_lap_number']}" for s in pair], pair


def test_oversteer_share_and_worst_5_percent():
    # Mostly understeer, with a short oversteer moment the average hides
    bal = np.array([2.0] * 90 + [-3.0] * 10)
    st = balance_stats(bal)
    assert st["balance_deg"] == pytest.approx(1.5)  # still looks like pure understeer...
    assert st["oversteer_pct"] == 10.0  # ...but 10% of the time it's oversteering
    assert st["balance_p5_deg"] == pytest.approx(-3.0)  # and the worst 5% is 3° the other way
    assert balance_stats(None)["oversteer_pct"] is None and balance_stats(np.array([]))["balance_p5_deg"] is None


def test_metrics_api_on_real_laps():
    client, laps, pair = _real_laps()
    r = client.get("/api/metrics", params={"lap": laps, "ls_max_kph": 60, "ms_max_kph": 90, "power_rule": "and"})
    assert r.status_code == 200
    m = r.json()
    assert m["settings"]["ls_max_kph"] == 60 and m["gates"][0] == "Braking"
    for lap, s in zip(m["laps"], pair):
        assert sum(g["distance_pct"] for g in lap["gates"]) == pytest.approx(100, abs=0.5)
        # time in all gates adds up to the whole lap
        assert sum(g["time_s"] for g in lap["gates"]) == pytest.approx(s["best_lap_s"], abs=0.005)
        assert len(lap["gg"]["lat"]) == len(lap["gg"]["gate"]) > 100
    assert len(m["gate_map"]["x"]) == len(m["gate_map"]["gate"])
    # how far to trust the balance comes from the backtest's baseline.json: a sentence next to the balance basis
    assert m["laps"][0]["balance_basis"] and m["laps"][0]["balance_accuracy"] and m["balance_accuracy"]
    for lap in m["laps"]:  # oversteer share and worst 5% sit next to every balance average
        for g in [lap["summary"], *lap["gates"]]:
            if g["balance_deg"] is not None:
                assert 0 <= g["oversteer_pct"] <= 100 and g["balance_p5_deg"] <= g["balance_deg"]


def test_mech_balance_per_corner_spread():
    # Three corners at 40, 45 and 50% front (same load moving), and a bit of straight between them
    front = np.r_[[400.0] * 10, [0] * 3, [450.0] * 10, [0] * 3, [500.0] * 10]
    total = np.r_[[1000.0] * 10, [0] * 3, [1000.0] * 10, [0] * 3, [1000.0] * 10]
    steady = total > 0
    out = mech_stats((front, total, steady), np.ones(len(front), bool), corner_numbers(steady))
    assert out["mech_balance_pct"] == pytest.approx(45.0) and out["corners"] == 3
    assert out["mech_balance_spread"] == pytest.approx(4.1, abs=0.05)  # std of 40, 45, 50
    # A left-hander moves load the other way (negative): same answer
    left = mech_stats((-front, -total, steady), steady, corner_numbers(steady))
    assert left["mech_balance_pct"] == pytest.approx(45.0)
    # Too little steady cornering to say, and no tyre loads at all (iRacing)
    assert mech_stats((front, total, steady), np.arange(len(front)) < 5, corner_numbers(steady))["mech_balance_pct"] is None
    assert mech_stats(None, steady, corner_numbers(steady)) == {"mech_balance_pct": None, "mech_balance_spread": None,
                                                                 "corners": 0}


def test_mech_balance_vs_lateral_g_bands():
    # 40% front up to 1 G, 50% above it (e.g. a front bump stop): shows as a step in the bands
    lat = np.r_[np.full(20, 0.8), np.full(20, 1.4)]
    total = 1000 * lat
    front = np.r_[0.4 * total[:20], 0.5 * total[20:]]
    bands = mech_vs_lat_g((front, total, np.ones(40, bool)), lat, 0.5)
    assert [(b.lat_g, b.front_pct, b.points) for b in bands] == [(0.875, 40.0, 20), (1.375, 50.0, 20)]
    assert mech_vs_lat_g(None, lat, 0.5) == []


def test_mech_balance_on_real_mft02_laps():
    """The MFT02 puts 43-46% of its lateral load transfer on the front (checked on 21 runs: 42.5-45.6%
    per best lap, 0.2-0.9 points lap to lap), much the same in every gate and from 0.5 G to 2.2 G."""
    client, _, _ = _real_laps()
    s = next((x for x in client.get("/api/sessions").json()
              if x["car"].startswith("madformulateam_mft02") and x["best_lap_number"]), None)
    if s is None:
        pytest.skip("no MFT02 runs on this machine")
    lap = client.get("/api/metrics", params={"lap": [f"{s['id']}:{s['best_lap_number']}"]}).json()["laps"][0]
    summary = lap["summary"]
    assert 42 < summary["mech_balance_pct"] < 46.5 and 0 < summary["mech_balance_spread"] < 6
    assert summary["mech_corners"] >= 5
    assert all(g["mech_balance_pct"] is None or 35 < g["mech_balance_pct"] < 55 for g in lap["gates"])
    assert len(lap["mech_vs_lat_g"]) >= 3 and all(35 < b["front_pct"] < 55 for b in lap["mech_vs_lat_g"])


def test_no_mech_balance_without_tyre_loads():
    """iRacing doesn't log tyre loads: no mech balance, rather than a made-up one."""
    from fs_telemetry.config import IRACING_DIR
    from fs_telemetry.main import app

    if not (IRACING_DIR and IRACING_DIR.exists() and any(IRACING_DIR.rglob("*.ibt"))):
        pytest.skip("no .ibt files on this machine")
    client = TestClient(app)
    s = next((x for x in client.get("/api/sessions").json() if x["source"] == "iracing" and x["best_lap_number"]), None)
    if s is None:
        pytest.skip("no timed iRacing laps")
    lap = client.get("/api/metrics", params={"lap": [f"{s['id']}:{s['best_lap_number']}"]}).json()["laps"][0]
    assert lap["summary"]["mech_balance_pct"] is None and lap["mech_vs_lat_g"] == []


def test_settings_change_the_answer_and_bad_settings_are_rejected():
    client, laps, _ = _real_laps()
    share = lambda params: {g["gate"]: g["distance_pct"] for g in
                            client.get("/api/metrics", params={"lap": laps[:1], **params}).json()["laps"][0]["gates"]}
    assert share({"power_rule": "and"})["Power limited"] < share({"power_rule": "or"})["Power limited"]
    assert client.get("/api/metrics", params={"lap": laps[:1], "ls_max_kph": 100, "ms_max_kph": 90}).status_code == 422
    # stint uses the same settings for driver inputs
    sid = laps[0].rsplit(":", 1)[0]
    ft = lambda pct: client.get(f"/api/sessions/{sid}/stint", params={"full_throttle_pct": pct}).json()["laps"][1]["full_throttle_pct"]
    assert ft(50) > ft(99.5)


def test_g_absolute_metrics_on_real_laps():
    """G absolute (sqrt(lat² + long²)): average and 95th percentile, per gate and per lap,
    on the Metrics page and in the stint."""
    client, laps, _ = _real_laps()
    lap = client.get("/api/metrics", params={"lap": laps[:1]}).json()["laps"][0]
    s = lap["summary"]
    assert 0 < s["g_abs_avg"] < s["g_abs_p95"] <= max(abs(x) for x in lap["gg"]["lat"]) + max(abs(x) for x in lap["gg"]["long"])
    assert all(g["g_abs_p95"] is None or g["g_abs_p95"] >= g["g_abs_avg"] for g in lap["gates"])
    sid = laps[0].rsplit(":", 1)[0]
    st = client.get(f"/api/sessions/{sid}/stint").json()
    timed = [l for l in st["laps"] if l["kind"] == "timed"]
    assert all(l["g_abs_p95"] > l["g_abs_avg"] > 0 for l in timed)
    assert st["summary"]["g_abs_avg"] is not None


def test_saved_layouts_with_renamed_channels_are_updated(tmp_path):
    from fs_telemetry.layouts import LayoutStore

    store = LayoutStore(tmp_path / "layouts.json")
    store.save("old", [["Combined G", "Ground Speed"]])
    saved = next(l for l in store.all() if l.name == "old")
    assert saved.panels == [["G Absolute", "Ground Speed"]]
