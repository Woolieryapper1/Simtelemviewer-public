"""Camber optimiser tests: made-up camber and load with a known best answer, and tyres.ini reading."""

import numpy as np
import pytest
from pydantic import ValidationError
from test_dampers import FakeCatalog, fake_session

from fs_telemetry.camber import (CamberSettings, axle_result, axle_score, camber_grip, compute_camber, corner_camber,
                                 offsets, peak_camber_deg, read_ini, temp_check, tyre_curve, tyres_for)
from fs_telemetry.compare import CompareError, LapRef
from fs_telemetry.ingest.motec_ld import Channel

TYRES_INI = """
[HEADER]
VERSION=10
[COMPOUND_DEFAULT]
INDEX=1
[FRONT]
NAME=Hard
DCAMBER_0=1.1      ; comment
DCAMBER_1=-13
[REAR]
NAME=Hard
DCAMBER_0=1.2
DCAMBER_1=-13
[FRONT_1]
NAME=Soft
DCAMBER_0=1.4
DCAMBER_1=-13
LS_EXPY=0.73
[REAR_1]
NAME=Soft
DCAMBER_0=1.66
DCAMBER_1=-13
"""
TYPED = dict(front_dcamber0=1.4, front_dcamber1=-13, rear_dcamber0=1.4, rear_dcamber1=-13)


@pytest.fixture
def car_dir(tmp_path):
    (tmp_path / "tyres.ini").write_text(TYRES_INI)
    return tmp_path


def with_camber(s, camber, load):
    """Add constant Camber / Tire Load channels per corner: camber, load = {corner: value}."""
    t = s.channels["Lap Distance"].data
    for c in camber:
        s.channels[f"Camber {c}"] = Channel(f"Camber {c}", "deg", 20, np.full(len(t), float(camber[c])))
        s.channels[f"Tire Load {c}"] = Channel(f"Tire Load {c}", "N", 20, np.full(len(t), float(load[c])))
    return s


def run(cat, *refs, data_dir=None, **settings):
    return compute_camber(cat, [LapRef.parse(r) for r in refs], CamberSettings(**settings), data_dir=data_dir)


# --- The tyre ---------------------------------------------------------------------------


def test_peak_and_gain_from_the_parabola():
    assert peak_camber_deg(1.4, -13) == pytest.approx(-np.degrees(1.4 / 26))  # about -3.08 deg
    curve = tyre_curve("Front", 1.4, -13, "x")
    assert curve.peak_gain_pct == pytest.approx(100 * 1.4 ** 2 / 52, abs=0.01)
    assert max(curve.grip_pct) == pytest.approx(100, abs=0.01)
    # symmetric in the sign of camber, and highest at the peak
    assert camber_grip(-2.0, 1.4, -13) == pytest.approx(camber_grip(2.0, 1.4, -13))
    assert camber_grip(curve.peak_deg, 1.4, -13) > camber_grip(curve.peak_deg + 0.5, 1.4, -13)


def test_a_curve_without_a_peak_is_refused():
    with pytest.raises(CompareError, match="negative"):
        tyre_curve("Front", 1.2, 13, "x")


def test_reads_ini_with_comments_and_compounds(car_dir):
    ini = read_ini(TYRES_INI)
    assert ini["FRONT"]["DCAMBER_0"] == "1.1"
    name, names, curves = tyres_for("car", CamberSettings(), car_dir)
    assert (name, names) == ("Soft", ["Hard", "Soft"])  # COMPOUND_DEFAULT picks the soft
    assert [(c.axle, c.dcamber0) for c in curves] == [("Front", 1.4), ("Rear", 1.66)]
    name, _, curves = tyres_for("car", CamberSettings(compound=0), car_dir)
    assert name == "Hard" and curves[0].dcamber0 == 1.1
    with pytest.raises(CompareError, match="no compound 5"):
        tyres_for("car", CamberSettings(compound=5), car_dir)


def test_load_sensitivity_comes_from_the_ini_and_can_be_overridden(car_dir):
    _, _, curves = tyres_for("car", CamberSettings(), car_dir)
    assert [c.ls_expy for c in curves] == [0.73, 1.0]  # no LS_EXPY in the rear section: count load linearly
    _, _, curves = tyres_for("car", CamberSettings(ls_expy=0.8), car_dir)
    assert [c.ls_expy for c in curves] == [0.8, 0.8]
    assert tyres_for("unknown car", CamberSettings(**TYPED))[2][0].ls_expy == 1.0


def test_typed_numbers_win_over_the_ini(car_dir):
    _, _, curves = tyres_for("car", CamberSettings(rear_dcamber0=2.0, rear_dcamber1=-20), car_dir)
    assert curves[0].source.startswith("tyres.ini") and (curves[1].dcamber0, curves[1].source) == (2.0, "typed in")
    name, _, _ = tyres_for("unknown car", CamberSettings(**TYPED))  # no files needed at all
    assert name == "typed in"


def test_missing_tyres_ini_says_how_to_get_it(tmp_path):
    with pytest.raises(CompareError, match="Content Manager"):
        tyres_for("tatuusfa1", CamberSettings(), tmp_path)


def test_typed_numbers_come_in_pairs():
    with pytest.raises(ValidationError):
        CamberSettings(front_dcamber0=1.2)


# --- The maths ---------------------------------------------------------------------------


def test_best_change_brings_constant_camber_to_the_peak():
    curve = tyre_curve("Front", 1.4, -13, "x")
    deltas = offsets(CamberSettings(span_deg=3, step_deg=0.1))
    score = axle_score(np.full(100, -1.0), np.full(100, 3000.0), deltas, curve)
    assert deltas[np.nanargmax(score)] == pytest.approx(round(curve.peak_deg + 1.0, 1))  # -2.1: 2.1 deg more negative
    assert np.nanmax(score) == pytest.approx(100, abs=0.05)


def test_the_loaded_tyre_counts_most():
    curve = tyre_curve("Front", 1.4, -13, "x")
    deltas = offsets(CamberSettings())
    cam = np.r_[np.full(50, -1.0), np.full(50, -4.0)]  # outside tyre at -1, inside at -4
    heavy = np.r_[np.full(50, 5000.0), np.full(50, 500.0)]
    best = deltas[np.nanargmax(axle_score(cam, heavy, deltas, curve))]
    assert -2.1 <= best < -1.5  # most of the way to putting the loaded tyre on the peak


def test_load_counts_by_its_sensitivity_exponent_not_linearly():
    deltas = offsets(CamberSettings())
    cam = np.r_[np.full(50, -1.0), np.full(50, -4.0)]  # outside tyre at -1, inside at -4
    load = np.r_[np.full(50, 5000.0), np.full(50, 500.0)]
    best = {e: deltas[np.nanargmax(axle_score(cam, load, deltas, tyre_curve("Front", 1.4, -13, "x", e)))] for e in (1.0, 0.7)}
    assert best[0.7] > best[1.0]  # the lightly loaded inside tyre (already past the peak at -4) counts for more: less extra negative camber
    # the weighted mean camber is exactly what the best change moves to the peak
    peak = tyre_curve("Front", 1.4, -13, "x").peak_deg
    for e, b in best.items():
        w = np.maximum(load, 0) ** e
        assert b == pytest.approx(round(peak - np.average(cam, weights=w), 1), abs=0.11)


def test_corner_summary_is_load_weighted():
    cc = corner_camber("FL", np.array([-1.0, -3.0]), np.array([3000.0, 1000.0]))
    assert cc.mean_deg == pytest.approx(-1.5)
    assert corner_camber("FL", np.array([]), np.array([])).mean_deg is None
    assert corner_camber("FL", np.array([-1.0]), np.array([1.0]), calm=np.array([-2.0, -4.0])).calm_deg == pytest.approx(-3.0)


def test_signed_camber_penalises_the_inside_tyre_going_positive():
    assert camber_grip(1.0, 1.4, -13) == pytest.approx(camber_grip(-1.0, 1.4, -13))  # abs: the sign doesn't matter
    assert camber_grip(1.0, 1.4, -13, signed=True) < 1.0 < camber_grip(-1.0, 1.4, -13, signed=True)
    assert camber_grip(-2.0, 1.4, -13, signed=True) == pytest.approx(camber_grip(-2.0, 1.4, -13))  # same on the negative side
    deltas = offsets(CamberSettings())
    cam = np.r_[np.full(50, -3.0), np.full(50, 0.5)]  # outside tyre on the peak, inside tyre rolled to +0.5
    load = np.r_[np.full(50, 3000.0), np.full(50, 3000.0)]
    best = {sg: deltas[np.nanargmax(axle_score(cam, load, deltas, tyre_curve("Front", 1.4, -13, "x", signed=sg)))] for sg in (False, True)}
    assert best[True] < best[False]  # signed: the inside tyre wants more negative camber, so the axle goes further that way


def test_inside_tyre_setting_reaches_the_curves(car_dir):
    assert not any(c.signed for c in tyres_for("car", CamberSettings(), car_dir)[2])
    assert all(c.signed for c in tyres_for("car", CamberSettings(inside_tyre="signed"), car_dir)[2])
    assert all(c.signed for c in tyres_for("car", CamberSettings(inside_tyre="signed", **TYPED))[2])


def test_a_flat_score_is_not_worthwhile():
    curve = tyre_curve("Front", 1.4, -13, "x")
    deltas = offsets(CamberSettings())
    near = axle_result("Front", [np.full(100, -2.9)], [np.full(100, 3000.0)], deltas, curve, min_gain_pct=0.5)
    far = axle_result("Front", [np.full(100, -0.5)], [np.full(100, 3000.0)], deltas, curve, min_gain_pct=0.5)
    assert near.best_offset_deg is not None and not near.worthwhile and near.gain_pct < 0.5
    assert far.worthwhile and far.gain_pct > 0.5


def test_tread_temperatures_can_argue_against_a_change():
    assert temp_check(-1.0, 18.0)[0] and "more negative" in temp_check(-1.0, 18.0)[1]  # inner already hot
    assert temp_check(1.0, -6.0)[0]  # outer hot, asked for less negative camber
    assert not temp_check(-1.0, 5.0)[0] and not temp_check(-1.0, None)[0] and not temp_check(0.0, 20.0)[0]


# --- From a session -------------------------------------------------------------------------


def test_end_to_end_on_a_fake_run():
    s = with_camber(fake_session(), {"FL": -1, "FR": -1, "RL": -2, "RR": -2}, dict.fromkeys(["FL", "FR", "RL", "RR"], 3000))
    d = run(FakeCatalog(A=s), "A:2", **TYPED)
    sel = d.selections[0]
    assert sel.laps == [2, 3, 4, 5] and sel.time_s > 0  # every timed lap of the run by default (1 is the out lap)
    front, rear = sel.axles
    assert front.best_offset_deg == pytest.approx(-2.1) and rear.best_offset_deg == pytest.approx(-1.1)
    assert front.current_pct < front.best_pct and not front.at_limit
    assert [c.mean_deg for c in sel.corners] == [-1, -1, -2, -2]


def by_distance(s, name, near, far, split_m):
    """Set a channel (on the Lap Distance clock) to `near` over the first split_m metres of every lap, else `far`."""
    d = s.channels["Lap Distance"].data
    s.channels[name] = Channel(name, s.channels[name].unit, s.channels[name].freq, np.where(d < split_m, near, far).astype(float))


def test_gate_counts_only_that_part_of_the_lap():
    s = with_camber(fake_session(brake_until_m=300), dict.fromkeys(["FL", "FR", "RL", "RR"], -3), dict.fromkeys(["FL", "FR", "RL", "RR"], 3000))
    by_distance(s, "Camber FL", -1, -3, 300)  # FL runs -1 while braking, -3 everywhere else
    cat = FakeCatalog(A=s)
    whole, braking = (run(cat, "A:2", gate=g, **TYPED).selections[0] for g in ("all", "Braking"))
    assert braking.corners[0].mean_deg == pytest.approx(-1, abs=0.05)
    assert whole.corners[0].mean_deg == pytest.approx(-2.4, abs=0.05)  # 30% of the lap at -1, 70% at -3
    assert braking.time_s == pytest.approx(0.3 * whole.time_s, rel=0.03)


def test_g_band_and_peak_percentile():
    s = with_camber(fake_session(), dict.fromkeys(["FL", "FR", "RL", "RR"], -3), dict.fromkeys(["FL", "FR", "RL", "RR"], 3000))
    by_distance(s, "CG Accel Lateral", 0.9, 1.5, 700)  # 70% of the lap at 0.9 g, 30% at 1.5 g
    by_distance(s, "Camber FL", -1, -3, 700)
    cat = FakeCatalog(A=s)
    near_peak = run(cat, "A:2", **TYPED).selections[0]  # >= 0.6 x 1.5 g: everything
    below = run(cat, "A:2", max_g=0.8, **TYPED).selections[0]  # 0.9..1.2 g: only the 0.9 g part
    assert near_peak.peak_g == pytest.approx(1.5) and near_peak.corners[0].mean_deg == pytest.approx(-1.6, abs=0.05)
    assert below.corners[0].mean_deg == pytest.approx(-1, abs=0.01)
    assert run(cat, "A:2", peak_pct=50, **TYPED).selections[0].peak_g == pytest.approx(0.9)
    with pytest.raises(ValidationError):
        CamberSettings(min_g=0.6, max_g=0.5)


def test_cornering_only_by_default_but_the_braking_gate_uses_combined_g():
    s = with_camber(fake_session(brake_until_m=300), dict.fromkeys(["FL", "FR", "RL", "RR"], -3), dict.fromkeys(["FL", "FR", "RL", "RR"], 3000))
    by_distance(s, "CG Accel Lateral", 0.3, 1.0, 300)  # braking: little lateral G, big longitudinal
    by_distance(s, "CG Accel Longitudinal", -2.0, 0.0, 300)
    by_distance(s, "Camber FL", -1, -3, 300)
    cat = FakeCatalog(A=s)
    lateral = run(cat, "A:2", **TYPED).selections[0]
    combined = run(cat, "A:2", g_basis="combined", **TYPED).selections[0]
    braking = run(cat, "A:2", gate="Braking", **TYPED).selections[0]
    assert lateral.g_basis == "lateral" and lateral.peak_g == pytest.approx(1.0) and lateral.corners[0].mean_deg == pytest.approx(-3, abs=0.05)
    assert combined.g_basis == "combined" and combined.corners[0].mean_deg == pytest.approx(-1, abs=0.05)  # only braking counts
    assert braking.g_basis == "combined" and braking.time_s > 0  # lateral G alone would leave nothing in this gate


def test_calm_camber_and_tread_temperature_are_reported():
    s = with_camber(fake_session(), dict.fromkeys(["FL", "FR", "RL", "RR"], -3), dict.fromkeys(["FL", "FR", "RL", "RR"], 3000))
    by_distance(s, "CG Accel Lateral", 0.05, 1.0, 300)
    by_distance(s, "CG Accel Longitudinal", 0.0, 0.0, 300)
    by_distance(s, "Camber FL", -1.5, -3, 300)  # the camber on the calm part of the lap
    for c in ("FL", "FR", "RL", "RR"):
        t = np.full(len(s.channels["Lap Distance"].data), 80.0)
        s.channels[f"Tire Temp Outer {c}"] = Channel(f"Tire Temp Outer {c}", "C", 20, t)
        s.channels[f"Tire Temp Inner {c}"] = Channel(f"Tire Temp Inner {c}", "C", 20, t + 15)
    sel = run(FakeCatalog(A=s), "A:2", **TYPED).selections[0]
    assert sel.corners[0].calm_deg == pytest.approx(-1.5, abs=0.01) and sel.corners[1].calm_deg == pytest.approx(-3)
    front = sel.axles[0]
    assert front.spread_c == pytest.approx(15) and front.best_offset_deg < 0 and front.temp_conflict  # inner already hot
    assert "more negative" in front.temp_note


def test_best_beyond_the_range_is_flagged():
    s = with_camber(fake_session(), dict.fromkeys(["FL", "FR", "RL", "RR"], 0), dict.fromkeys(["FL", "FR", "RL", "RR"], 3000))
    front = run(FakeCatalog(A=s), "A:2", span_deg=1, **TYPED).selections[0].axles[0]
    assert front.best_offset_deg == -1 and front.at_limit


def test_changes_that_make_the_camber_positive_are_not_tried():
    curve = tyre_curve("Front", 1.4, -13, "x")
    deltas = offsets(CamberSettings(span_deg=3, step_deg=0.5))
    score = axle_score(np.full(10, -1.0), np.full(10, 3000.0), deltas, curve)
    assert np.isnan(score[deltas > 1.0]).all() and not np.isnan(score[deltas <= 1.0]).any()


def test_runs_without_camber_say_so():
    s = fake_session()
    s.source = "iracing"
    with pytest.raises(CompareError, match="iRacing doesn't log camber"):
        run(FakeCatalog(A=s), "A:2", **TYPED)
