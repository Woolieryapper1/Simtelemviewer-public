"""The balance backtest (backtest/adapters/balance.py) on made-up runs with a known balance, and the
sentence about how far to trust a balance number (accuracy.py). Quick: synthetic runs only."""

import numpy as np
import pytest

from fs_telemetry import maths
from fs_telemetry.accuracy import balance_statement
from fs_telemetry.backtest.adapters import ADAPTERS, balance
from fs_telemetry.backtest.adapters.balance_synthetic import BalanceCar, balance_runs, make_session
from fs_telemetry.backtest.harness import applies, gate, load_baseline, run_adapters
from fs_telemetry.backtest.synthetic import synthetic_runs
from fs_telemetry.backtest.validate import BASELINE

CAR = "synthetic balance car"


@pytest.fixture(scope="module")
def summary():
    results = run_adapters([balance.ADAPTER], balance_runs())
    assert len(results) == 1 and results[0].source == "synthetic"
    assert results[0].summary["runs_estimated_pct"] == 100
    return results[0].summary


def test_the_measures_follow_the_true_balance_on_a_car_with_a_known_answer(summary):
    # the made-up car has no noise in how it understeers, so the checks should find it nearly perfectly
    assert summary[f"r_understeer [{CAR}]"] > 0.99
    assert summary[f"r_steering [{CAR}]"] > 0.95
    assert abs(summary[f"bias_understeer_deg [{CAR}]"]) < 0.05
    assert summary[f"sign_ok_pct_understeer [{CAR}]"] == 100 and summary[f"sign_ok_pct_steering [{CAR}]"] == 100
    assert 0.95 < summary[f"scale_understeer [{CAR}]"] < 1.05


def test_the_steering_reference_reads_high_so_the_steering_measure_reads_low(summary):
    # the real-data finding, reproduced in the model: a car that understeers a little even in gentle
    # corners makes the learned reference too big, so the measure reads under the truth
    assert summary[f"k_ratio_vs_fit_pct [{CAR}]"] > 5
    assert summary[f"bias_steering_deg [{CAR}]"] < -0.1


def test_the_ratio_is_found_and_settles_after_a_few_corners(summary):
    assert summary[f"ratio_fit_error_vs_known_pct [{CAR}]"] == pytest.approx(0, abs=0.5)
    assert summary[f"ratio_fit_corners_to_settle [{CAR}]"] <= 8
    slow = f"{CAR} (slow rack)"
    assert summary[f"ratio_fit_error_vs_known_pct [{slow}]"] == pytest.approx(0, abs=0.5)  # 8:1, not 5:1


def test_a_known_setup_change_moves_every_measure_the_right_way(summary):
    assert summary["perturbation_right_way_pct"] == 100
    for name in ("stiffer front ARB", "stiffer rear ARB", "softer front ARB", "softer rear ARB"):
        assert summary[f"perturbation_size_ratio_slip [{name}]"] == pytest.approx(1, abs=0.1)
        assert summary[f"perturbation_size_ratio_understeer [{name}]"] == pytest.approx(1, abs=0.1)
        assert summary[f"perturbation_size_ratio_steering [{name}]"] > 0.3  # moves the right way, but by less


def test_noise_floor_is_tiny_when_nothing_real_changes(summary):
    assert summary[f"noise_sd_slip_deg [{CAR}]"] < 0.1
    assert summary[f"mdc_understeer_3laps_deg [{CAR}]"] < 0.2


def test_oversteer_stats_agree_with_the_truth_on_the_car_that_oversteers(summary):
    over = f"{CAR} (oversteery)"
    assert summary[f"oversteer_pct_T0.5_slip [{over}]"] > 50  # it oversteers most of the time
    assert summary[f"oversteer_pct_T0.5_understeer [{over}]"] == pytest.approx(summary[f"oversteer_pct_T0.5_slip [{over}]"], abs=5)


def test_the_understeer_formula_is_a_over_ratio_minus_b():
    """The backtest keeps steering (a) and the path (b) apart so it can try any ratio: check that
    really is the app's Understeer Angle."""
    car = BalanceCar()
    s = make_session(car, seed=1)
    wheelbase = car.wheelbase_m
    balance._test_channels(s, wheelbase)
    freq, x = maths._on_one_clock([s.channels[i] for i in balance.INPUTS])
    app = maths.understeer_angle(car.steering_ratio, wheelbase)(x)
    mine = s.channels["bt a"].data / car.steering_ratio - s.channels["bt b"].data
    assert np.allclose(app, mine)


def test_the_synthetic_balance_runs_pass_the_committed_baseline():
    results = run_adapters([balance.ADAPTER], balance_runs())
    assert gate(results, load_baseline(BASELINE)), [(v.metric, v.value, v.baseline, v.why)
                                                    for r in results for v in r.verdicts if v.status == "FAIL"]


def test_each_adapter_only_measures_its_own_synthetic_runs():
    aero = next(a for a in ADAPTERS if a.name == "aero_fit")
    assert all(applies(balance.ADAPTER, r) for r in balance_runs())
    assert not any(applies(aero, r) for r in balance_runs())
    assert not any(applies(balance.ADAPTER, r) for r in synthetic_runs())
    assert all(applies(aero, r) for r in synthetic_runs())


# --- Fitting the ratio from the first few corners -------------------------------------


def test_first_corners_keeps_only_the_first_stretches():
    use = np.array([0, 1, 1, 0, 1, 0, 0, 1, 1, 1], bool)
    assert list(maths.first_corners(use, 2).astype(int)) == [0, 1, 1, 0, 1, 0, 0, 0, 0, 0]
    assert list(maths.first_corners(use, None)) == list(use)


def test_a_fit_from_few_corners_is_missing_or_close_to_the_full_fit():
    s = make_session(BalanceCar(), seed=1)
    assert maths.fit_steering_ratio(s, 1.6, corners=1) is None  # too little to go on
    assert maths.fit_steering_ratio(s, 1.6, corners=10) == pytest.approx(maths.fit_steering_ratio(s, 1.6), rel=0.02)


# --- The sentence shown in the app ----------------------------------------------------

FAKE = {"ac": {"metrics": {
    "laps [kart]": 40, "r_steering [kart]": 0.9, "bias_steering_deg [kart]": -1.0, "r_understeer [kart]": 0.95,
    "bias_understeer_deg [kart]": -0.1, "noise_sd_steering_deg [kart]": 0.3, "noise_sd_slip_deg [kart]": 0.3,
    "trust_lat_g_steering [kart]": 1.1,
    "laps [rare]": 3, "r_steering [rare]": 1.0,
    "laps [kart2]": 20, "r_steering [kart2]": 0.8, "bias_steering_deg [kart2]": 0.0,
}}, "iracing": {"metrics": {"k_cv_pct [Cup]": 12.0}}}


def test_the_sentence_for_a_checked_car_quotes_its_own_numbers():
    text = balance_statement("steering", "kart", FAKE)
    assert "r = 0.90 on AC runs of this car" in text and "40 laps" in text
    assert "1.0° low" in text and "1.1 G" in text and "±0.6°" in text
    understeer = balance_statement("understeer", "kart", FAKE)
    assert "r = 0.95" in understeer and "low" not in understeer  # a 0.1 degree offset isn't worth a mention


def test_the_sentence_for_another_car_uses_all_the_checked_cars_and_says_so():
    text = balance_statement("steering", "Cup", FAKE)
    assert "r = 0.80-0.90" in text and "this car hasn't been checked" in text and "±12%" in text
    assert "rare" not in text  # too few laps to count


def test_slip_is_the_real_thing_and_nothing_to_say_without_a_baseline():
    assert balance_statement("slip", "kart", FAKE).startswith("Measured from the logged slip angles.")
    assert balance_statement("steering", "kart", {}) is None and balance_statement(None, "kart", FAKE) is None
