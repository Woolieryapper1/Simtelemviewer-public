"""Maths channel tests: formulas on made-up numbers, then real-data sanity checks."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fs_telemetry import maths
from fs_telemetry.ingest.motec_ld import Channel, LdSession

A = lambda *v: np.array(v, dtype=float)


def test_pedal_channels():
    x = {"Throttle Pos": A(100, 30, 0, 0), "Brake Pos": A(0, 20, 50, 1)}
    assert list(maths.overlap(x)) == [0, 20, 0, 0]
    assert list(maths.coasting(x)) == [0, 0, 0, 1]


def test_grip_channels():
    assert maths.g_absolute({"CG Accel Lateral": A(3), "CG Accel Longitudinal": A(-4)})[0] == pytest.approx(5)
    slip = {"Tire Slip Ratio RL": A(10, -5), "Tire Slip Ratio RR": A(4, -8), "Tire Slip Ratio FL": A(0, -30), "Tire Slip Ratio FR": A(1, -12)}
    assert list(maths.rear_wheelspin(slip)) == [10, 0]
    assert list(maths.front_lockup(slip)) == [0, 30]


def test_yaw_rate_excess_sign():
    # AC convention: +1 g lateral comes with NEGATIVE yaw rate. At 20 m/s the
    # force asks for -9.81/20 rad/s = -28.1 deg/s.
    base = {"Ground Speed": A(72, 72, 72, 5), "CG Accel Lateral": A(1, 1, 1, 1)}
    x = {**base, "Chassis Yaw Rate": A(-35, -20, -28.1, -10)}
    out = maths.yaw_excess(x)
    assert out[0] > 5  # rotating more than the path -> oversteer -> positive
    assert out[1] < -5  # rotating less -> understeer -> negative
    assert abs(out[2]) < 0.1
    assert out[3] == 0  # crawling: not meaningful, reported as 0


def corner_run(n_level=30, n_corner=30, lat_g=1.2, front_n=600.0, rear_n=400.0, offset=(0.0, 0.0), downforce=0.0):
    """Made-up run at 10 Hz: level running, then a steady corner where front_n / rear_n
    (left minus right) move across. offset: left-right difference at rest (front, rear);
    downforce: extra load on every tyre in the corner."""
    lat = np.r_[np.zeros(n_level), np.full(n_corner, lat_g)]
    front, rear = np.r_[np.zeros(n_level), np.full(n_corner, front_n)], np.r_[np.zeros(n_level), np.full(n_corner, rear_n)]
    aero = np.r_[np.zeros(n_level), np.full(n_corner, downforce)]
    return {"Time": np.arange(n_level + n_corner) / 10, "CG Accel Lateral": lat,
            "CG Accel Longitudinal": np.zeros(n_level + n_corner),
            "Tire Load FL": 1000 + offset[0] + front / 2 + aero, "Tire Load FR": 1000 - front / 2 + aero,
            "Tire Load RL": 1200 + offset[1] + rear / 2 + aero, "Tire Load RR": 1200 - rear / 2 + aero}


def test_mech_balance_is_front_share_of_load_moving_across():
    # 600 N moves across at the front, 400 N at the rear -> 60% front. Level running: 0.
    out = maths.mech_balance(corner_run())
    assert out[0] == 0 and out[-1] == pytest.approx(60.0)


def test_mech_balance_ignores_downforce_and_the_static_left_right_difference():
    # Downforce loads both sides equally; a driver sitting left adds load on the left even at rest
    out = maths.mech_balance(corner_run(downforce=800, offset=(40, 25)))
    assert out[-1] == pytest.approx(60.0)


def test_mech_balance_leaves_out_turn_in_and_hard_braking():
    x = corner_run()
    x["CG Accel Lateral"][30:33] = [0.6, 0.9, 1.2]  # turning in: lateral G rising 3 G/s
    x["CG Accel Longitudinal"][-5:] = -1.0  # trail braking hard at the end
    out = maths.mech_balance(x)
    assert np.all(out[30:33] == 0) and np.all(out[-5:] == 0) and out[40] == pytest.approx(60.0)


def test_brake_balance_and_balance_sign():
    tq = {"Brake Torque FL": A(300, 0), "Brake Torque FR": A(300, 0), "Brake Torque RL": A(150, 0), "Brake Torque RR": A(150, 0)}
    assert list(maths.brake_balance(tq)) == [pytest.approx(66.667, abs=0.01), 0]
    angles = {"Tire Slip Angle FL": A(6), "Tire Slip Angle FR": A(-6), "Tire Slip Angle RL": A(4), "Tire Slip Angle RR": A(-4)}
    assert maths.slip_balance(angles)[0] == 2  # fronts slipping more = understeer = +


def test_understeer_angle_sign():
    # 1.5 m wheelbase at 20 m/s turning left at 0.2 rad/s: the path needs 0.015 rad = 0.86 deg
    # of front wheel angle. With a 5:1 ratio that's 4.3 deg of steering wheel.
    f = maths.understeer_angle(steering_ratio=5.0, wheelbase_m=1.5)
    yaw = np.degrees(0.2)
    x = {"Ground Speed": A(72, 72, 72, 72, 5), "Chassis Yaw Rate": A(yaw, yaw, -yaw, -yaw, yaw),
         "Steering Angle": A(14.3, 0, -14.3, -2, 30)}
    out = f(x)
    assert out[0] == pytest.approx(2.0, abs=0.01)  # 2 deg more than needed, turning left -> understeer +
    assert out[1] < 0  # steering less than the path needs -> oversteer (countersteer) -
    assert out[2] == pytest.approx(2.0, abs=0.01)  # same understeer turning right
    assert out[3] < 0
    assert out[4] == 0  # crawling: not meaningful


def test_understeer_angle_only_when_the_steering_ratio_is_known(monkeypatch):
    from fs_telemetry import cars

    monkeypatch.setitem(cars.CARS, "table_car", cars.Car(steering_ratio=5.0))

    def session(car, details):
        chans = {n: Channel(n, "", 10, np.full(20, 50.0)) for n in ("Steering Angle", "Chassis Yaw Rate", "Ground Speed")}
        return LdSession(path=None, driver="", car=car, track="", date="", time="", channels=chans, details=details)

    known = maths.add_maths_channels(session("table_car", {"Vehicle Wheelbase": "1.545"}))
    assert "Understeer Angle" in known.channels and not known.channels["Understeer Angle"].approximate  # ratio from the table
    # not in the table, no AC files, no slip angles to fit the ratio from
    assert "Understeer Angle" not in maths.add_maths_channels(session("some_other_car", {"Vehicle Wheelbase": "2.5"})).channels
    assert "Understeer Angle" not in maths.add_maths_channels(session("table_car", {})).channels  # no wheelbase


def test_added_on_one_clock_skipped_if_inputs_missing_and_never_overwrite():
    s = LdSession(path=None, driver="", car="", track="", date="", time="", channels={
        "Throttle Pos": Channel("Throttle Pos", "%", 20, np.full(40, 50.0)),  # 2 s at 20 Hz
        "Brake Pos": Channel("Brake Pos", "%", 10, np.full(20, 10.0)),  # 2 s at 10 Hz
        "Rake": Channel("Rake", "mm", 1, A(1, 2)),  # pretend the logger already has a "Rake"
    })
    maths.add_maths_channels(s)
    ov = s.channels["Throttle Brake Overlap"]
    assert ov.maths and ov.freq == 20 and len(ov.data) == 40 and np.all(ov.data == 10)
    assert "G Absolute" not in s.channels  # inputs missing -> skipped
    assert not s.channels["Rake"].maths  # logged channel kept as it was


# --- Real data ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def real():
    from fs_telemetry.config import TELEMETRY_DIR
    from fs_telemetry.main import app, catalog

    if not (TELEMETRY_DIR.exists() and any(TELEMETRY_DIR.rglob("*.ld"))):
        pytest.skip("no .ld files on this machine")
    return TestClient(app), catalog


def test_every_session_gets_finite_maths_channels(real):
    _, catalog = real
    for summary in [x for x in catalog.summaries() if x.source == "ac"]:  # iRacing: see test_ibt.py
        s = catalog.get(summary.id)
        made = [c for c in s.channels.values() if c.maths]
        from fs_telemetry.aero import UNITS as AERO
        expected = {m.name for m in maths.CHANNELS + maths.car_channels(s) if not m.approximate or m.name in ("Understeer Angle", "Mech Balance (est.)")}
        dampers = {m.name for m in maths.damper_channels(s)}  # AC doesn't log damper velocity: estimated from travel
        assert {c.name for c in made} == expected | {f"{k} (est.)" for k in AERO} | dampers
        for c in made:
            assert np.all(np.isfinite(c.data)), f"{summary.id}: {c.name} has NaN/inf"


def test_actual_brake_balance_matches_the_setup_bias(real):
    """The hybrid runs used setups with FRONT_BIAS=65 (also in the Brake Bias channel)."""
    _, catalog = real
    checked = 0
    for summary in [x for x in catalog.summaries() if x.car.endswith("_hy")][:5]:
        s = catalog.get(summary.id)
        bb = s.channels["Brake Balance Actual"].data
        setting = float(np.median(s.channels["Brake Bias"].data))
        assert np.median(bb[bb > 0]) == pytest.approx(setting, abs=1.5)
        checked += 1
    assert checked


def test_understeer_angle_agrees_with_slip_angle_balance(real):
    """In theory understeer angle = front minus rear slip angle, so on the MFT02 (which logs
    slip angles) the steering-based channel should follow Balance Slip Angle in corners."""
    _, catalog = real
    checked = 0
    for summary in [x for x in catalog.summaries() if x.car.startswith("madformulateam_mft02")][:4]:
        s = catalog.get(summary.id)
        us, bal = s.channels["Understeer Angle"], s.channels["Balance Slip Angle"]
        bal_on_us_clock = np.interp(us.time, bal.time, bal.data)
        cornering = us.data != 0
        assert np.corrcoef(us.data[cornering], bal_on_us_clock[cornering])[0, 1] > 0.4
        checked += 1
    assert checked


def test_api_lists_and_serves_maths_channels(real):
    client, _ = real
    s = next(x for x in client.get("/api/sessions").json() if x["best_lap_number"])
    chans = {c["name"]: c for c in client.get(f"/api/sessions/{s['id']}/channels").json()}
    assert chans["G Absolute"]["maths"] and chans["G Absolute"]["description"]
    assert not chans["Ground Speed"]["maths"]
    r = client.get(f"/api/sessions/{s['id']}/laps/{s['best_lap_number']}/data",
                   params={"channels": ["Yaw Rate Excess", "Coasting"]}).json()
    assert len(r["data"]["Yaw Rate Excess"]) == len(r["data"]["Distance"]) > 100
    assert set(r["data"]["Coasting"]) <= {0.0, 1.0}  # on/off channel isn't blended
    csv = client.get(f"/api/sessions/{s['id']}/laps/{s['best_lap_number']}/data.csv", params={"channels": ["Rake"]}).text
    assert csv.splitlines()[0] == "Distance [m],Time [s],Rake [mm]"
