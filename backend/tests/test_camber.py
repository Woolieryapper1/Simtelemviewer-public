"""Camber optimiser tests: made-up camber and load with a known best answer, and tyres.ini reading."""

import numpy as np
import pytest
from pydantic import ValidationError
from test_dampers import FakeCatalog, fake_session

from fs_telemetry.camber import (CamberSettings, axle_score, camber_grip, compute_camber, corner_camber, offsets,
                                 peak_camber_deg, read_ini, tyre_curve, tyres_for)
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
    return compute_camber(cat, [LapRef.parse(r) for r in refs], CamberSettings(**settings), data_dir)


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


def test_corner_summary_is_load_weighted():
    cc = corner_camber("FL", np.array([-1.0, -3.0]), np.array([3000.0, 1000.0]))
    assert cc.mean_deg == pytest.approx(-1.5)
    assert corner_camber("FL", np.array([]), np.array([])).mean_deg is None


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
