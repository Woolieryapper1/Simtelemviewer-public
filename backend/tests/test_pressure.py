"""Pressure optimiser tests: made-up pressure, temperature and load with known answers, and tyres.ini reading."""

import numpy as np
import pytest
from pydantic import ValidationError
from test_dampers import FakeCatalog, fake_session

from fs_telemetry.compare import CompareError, LapRef
from fs_telemetry.ingest.motec_ld import Channel
from fs_telemetry.pressure import (LapPoint, PressureSettings, TyreNumbers, compute_pressure, corner_pressure,
                                   measured_gain, parse_lut, pressure_loss_pct, round_to_step, temp_grip,
                                   temp_state, temp_window, tyres_for)

TYRES_INI = """
[HEADER]
VERSION=10
[COMPOUND_DEFAULT]
INDEX=0
[ADDITIONAL1]
PRESSURE_TEMPERATURE_GAIN=0.111
[FRONT]
NAME=Soft
PRESSURE_STATIC=12          ; cold
PRESSURE_D_GAIN=0.01
PRESSURE_IDEAL=14
LS_EXPY=0.71
[REAR]
NAME=Soft
PRESSURE_D_GAIN=0.01
PRESSURE_IDEAL=15
LS_EXPY=0.8
[FRONT_1]
NAME=Hard
PRESSURE_D_GAIN=0.02
PRESSURE_IDEAL=20
[REAR_1]
NAME=Hard
PRESSURE_D_GAIN=0.02
PRESSURE_IDEAL=21
[THERMAL_FRONT]
PERFORMANCE_CURVE=soft.lut
[THERMAL_REAR]
PERFORMANCE_CURVE=soft.lut
[THERMAL_FRONT_1]
PERFORMANCE_CURVE=hard.lut
[THERMAL_REAR_1]
PERFORMANCE_CURVE=hard.lut
"""
SETUP_INI = "[PRESSURE_LF]\nSTEP=1\n[PRESSURE_LR]\nSTEP=0.5\n"
SOFT_LUT = "0|0.85\n20|0.9\n40|0.97\n50|0.99\n55|1\n70|1\n75|0.99\n100|0.95\n120|0.85\n"
HARD_LUT = "0|0.5\n100|1\n200|0.5\n"
CORNERS = ("FL", "FR", "RL", "RR")


@pytest.fixture
def car_dir(tmp_path):
    (tmp_path / "tyres.ini").write_text(TYRES_INI)
    (tmp_path / "setup.ini").write_text(SETUP_INI)
    (tmp_path / "soft.lut").write_text(SOFT_LUT)
    (tmp_path / "hard.lut").write_text(HARD_LUT)
    return tmp_path


def tyre(ideal=14.0, d_gain=0.01, step=1.0, gain=0.111, ls=0.71):
    c = parse_lut(SOFT_LUT)
    return TyreNumbers(axle="Front", ideal_psi=ideal, d_gain=d_gain, static_psi=12, ls_expy=ls, psi_per_c=gain, step_psi=step,
                       window_c=temp_window(c), curve_c=[], curve_pct=[], source="test"), c


def with_tyres(s, hot, tread, load=3000.0):
    """Add constant Tire Pressure, tread temperature rows, core and Tire Load channels per corner (dicts per corner)."""
    n = len(s.channels["Lap Distance"].data)
    put = lambda name, unit, v: s.channels.__setitem__(name, Channel(name, unit, 20, np.broadcast_to(np.asarray(v, float), n).copy()))
    for c in CORNERS:
        put(f"Tire Pressure {c}", "psi", hot[c] if isinstance(hot, dict) else hot)
        put(f"Tire Load {c}", "N", load)
        put(f"Tire Temp Core {c}", "C", (tread[c] if isinstance(tread, dict) else tread) - 2.0)
        for row in ("Inner", "Middle", "Outer"):
            put(f"Tire Temp {row} {c}", "C", tread[c] if isinstance(tread, dict) else tread)
    return s


def run(cat, *refs, data_dir=None, **settings):
    return compute_pressure(cat, [LapRef.parse(r) for r in refs], PressureSettings(**settings), data_dir=data_dir)


# --- The tyre ---------------------------------------------------------------------------


def test_lut_is_read_and_flat_past_its_ends():
    c = parse_lut("0|0.5 ; comment\n\n10|1\n")
    assert temp_grip(5, c) == pytest.approx(0.75) and temp_grip(-50, c) == 0.5 and temp_grip(99, c) == 1.0
    with pytest.raises(ValueError):
        parse_lut("5|1")


def test_temperature_window_is_where_the_curve_is_within_one_percent():
    lo, hi = temp_window(parse_lut(SOFT_LUT))
    assert (lo, hi) == (50.0, 75.0)
    assert [temp_state(t, (lo, hi)) for t in (40, 60, 80)] == ["cool", "in window", "hot"]


def test_pressure_loss_is_linear_and_symmetric():
    assert pressure_loss_pct(15.0, 14, 0.01) == pytest.approx(1.0)
    assert pressure_loss_pct(13.0, 14, 0.01) == pytest.approx(1.0)
    assert pressure_loss_pct(14.0, 14, 0.01) == 0.0


def test_reads_tyres_ini_per_axle_compound_and_curve(car_dir):
    name, names, tyres, curves = tyres_for("car", PressureSettings(), car_dir)
    assert (name, names) == ("Soft", ["Soft", "Hard"])
    front, rear = tyres
    assert (front.ideal_psi, rear.ideal_psi, front.d_gain, front.psi_per_c) == (14, 15, 0.01, 0.111)
    assert (front.step_psi, rear.step_psi) == (1.0, 0.5)  # setup.ini sliders
    assert (front.ls_expy, rear.ls_expy, front.static_psi) == (0.71, 0.8, 12)
    assert front.window_c == (50.0, 75.0) and max(curves["Front"][1]) == 1.0
    name, _, tyres, curves = tyres_for("car", PressureSettings(compound=1), car_dir)
    assert name == "Hard" and tyres[0].ideal_psi == 20 and curves["Front"][0][-1] == 200  # FRONT_1 and hard.lut


def test_typed_numbers_win_over_the_ini(car_dir):
    _, _, tyres, _ = tyres_for("car", PressureSettings(ideal_psi=13.5, psi_per_c=0.2), car_dir)
    assert [(t.ideal_psi, t.psi_per_c) for t in tyres] == [(13.5, 0.2)] * 2


def test_missing_files_say_what_to_do(tmp_path, car_dir):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(CompareError, match="Content Manager"):
        tyres_for("tatuusfa1", PressureSettings(), empty)
    (car_dir / "soft.lut").unlink()
    with pytest.raises(CompareError, match="temperature curve"):
        tyres_for("car", PressureSettings(), car_dir)
    with pytest.raises(CompareError, match="no compound 7"):
        tyres_for("car", PressureSettings(compound=7), car_dir)


def test_g_band_must_be_ordered():
    with pytest.raises(ValidationError):
        PressureSettings(min_g=0.8, max_g=0.5)


# --- The maths ---------------------------------------------------------------------------


def test_rounding_to_the_slider_step():
    assert round_to_step(-1.2, 1.0) == -1.0 and round_to_step(-0.4, 1.0) == 0 and round_to_step(0.74, 0.5) == 0.5


def test_too_much_pressure_says_lower_the_cold_by_the_error():
    t, c = tyre()
    r = corner_pressure("FL", np.full(10, 15.2), np.full(10, 62.0), np.full(10, 60.0), np.full(10, 3000.0), t, c)
    assert (r.error_psi, r.change_psi, r.rounded_change_psi) == (1.2, -1.2, -1.0)
    assert r.loss_pct == pytest.approx(1.2) and r.left_pct == pytest.approx(0.2) and r.worthwhile
    assert r.temp_state == "in window" and r.temp_grip_pct == pytest.approx(100.0) and r.core_c == 60.0


def test_close_to_ideal_is_no_clear_change():
    t, c = tyre()
    r = corner_pressure("FL", np.full(10, 14.2), np.full(10, 62.0), None, np.full(10, 3000.0), t, c)
    assert r.change_psi == -0.2 and r.rounded_change_psi == 0 and not r.worthwhile  # a 1 psi step can't improve on it
    assert r.core_c is None


def test_scenarios_move_pressure_with_the_temperature_gain():
    t, c = tyre(gain=0.1)
    r = corner_pressure("FL", np.full(10, 14.0), np.full(10, 70.0), None, np.full(10, 3000.0), t, c)
    by = {s.shift_c: s for s in r.scenarios}
    assert by[0.0].cold_change_psi == 0 and by[0.0].loss_if_unchanged_pct == 0
    assert by[10.0].hot_psi == pytest.approx(15.0) and by[10.0].cold_change_psi == pytest.approx(-1.0)
    assert by[-10.0].cold_change_psi == pytest.approx(1.0)
    assert by[10.0].temp_grip_pct == pytest.approx(100 * temp_grip(80, c) / 1.0, abs=0.01)  # past the window: 97.1%
    assert by[10.0].loss_if_unchanged_pct == pytest.approx(1.0)


def test_the_loaded_moments_count_most():
    t, c = tyre(ls=1.0)
    hot = np.r_[np.full(50, 15.0), np.full(50, 13.0)]
    load = np.r_[np.full(50, 4500.0), np.full(50, 500.0)]
    r = corner_pressure("FL", hot, np.full(100, 60.0), None, load, t, c)
    assert r.hot_psi == pytest.approx(14.8)
    assert corner_pressure("FL", np.array([]), np.array([]), None, np.array([]), t, c).hot_psi is None


def test_measured_gain_recovers_the_slope_and_ignores_each_tyres_own_offset():
    temps = [55.0, 60.0, 65.0, 70.0]
    laps = [LapPoint(lap=i + 2, tread_c={c: T + k for k, c in enumerate(CORNERS)},
                     hot_psi={c: 12 + 0.12 * (T + k) + 0.7 * k for k, c in enumerate(CORNERS)}) for i, T in enumerate(temps)]
    assert measured_gain(laps) == pytest.approx(0.12, abs=0.001)
    assert measured_gain(laps[:3]) is None  # too few laps for a slope
    flat = [LapPoint(lap=i, tread_c=dict.fromkeys(CORNERS, 60.0), hot_psi=dict.fromkeys(CORNERS, 14.0)) for i in range(5)]
    assert measured_gain(flat) is None  # no spread in temperature


# --- From a session -------------------------------------------------------------------------


def test_end_to_end_on_a_fake_run(car_dir):
    hot = {"FL": 15.0, "FR": 14.0, "RL": 16.0, "RR": 15.0}
    s = with_tyres(fake_session(), hot, dict.fromkeys(CORNERS, 62.0))
    d = run(FakeCatalog(A=s), "A:2", data_dir=car_dir)
    sel = d.selections[0]
    assert sel.laps == [2, 3, 4, 5] and sel.time_s > 0 and sel.compound == "Soft"
    assert sel.temp_source == "mean of the inner, middle and outer tread"
    fl, fr, rl, rr = sel.corners
    assert (fl.hot_psi, fr.hot_psi, rl.hot_psi) == (15.0, 14.0, 16.0)
    assert (fl.change_psi, fr.change_psi, rl.change_psi, rr.change_psi) == (-1.0, 0.0, -1.0, 0.0)  # rear ideal is 15
    assert rl.rounded_change_psi == -1.0 and not fr.worthwhile and fl.worthwhile
    assert [p.lap for p in sel.by_lap] == [2, 3, 4, 5] and sel.by_lap[0].tread_c["FL"] == 62.0
    assert d.shifts_c == [-10, -5, 0, 5, 10] and "tyres.ini" in d.note


def test_only_the_high_g_part_of_the_lap_counts(car_dir):
    s = fake_session()
    dist = s.channels["Lap Distance"].data
    fast = dist < 500
    s.channels["CG Accel Lateral"] = Channel("CG Accel Lateral", "G", 20, np.where(fast, 2.0, 0.5))
    with_tyres(s, {"FL": 15.0, "FR": 15.0, "RL": 15.0, "RR": 15.0}, 60.0)
    s.channels["Tire Pressure FL"] = Channel("Tire Pressure FL", "psi", 20, np.where(fast, 15.0, 13.0))
    d = run(FakeCatalog(A=s), "A:2", data_dir=car_dir)
    assert d.selections[0].corners[0].hot_psi == pytest.approx(15.0)  # the 13 psi straight isn't near the limit
    d = run(FakeCatalog(A=s), "A:2", data_dir=car_dir, min_g=0.0)
    assert 13.5 < d.selections[0].corners[0].hot_psi < 14.5  # counting everything: in between


def test_core_is_used_when_there_are_no_tread_rows(car_dir):
    s = with_tyres(fake_session(), 14.0, 60.0)
    for c in CORNERS:
        for row in ("Inner", "Middle", "Outer"):
            del s.channels[f"Tire Temp {row} {c}"]
    d = run(FakeCatalog(A=s), "A:2", data_dir=car_dir)
    assert d.selections[0].temp_source == "core" and d.selections[0].corners[0].tread_c == 58.0


def test_runs_without_pressure_say_so(car_dir):
    with pytest.raises(CompareError, match="Tire Pressure FL"):
        run(FakeCatalog(A=fake_session()), "A:2", data_dir=car_dir)
