"""Damper optimiser tests: a made-up car whose damping ratios can be worked out by hand."""

import math

import numpy as np
import pytest
from test_dampers import FakeCatalog, fake_session

from fs_telemetry.compare import CompareError, LapRef
from fs_telemetry.damper_setup import (DamperSetupSettings, car_setup, compute_damper_setup, from_setup,
                                       load_variation_pct, setup_value)
from fs_telemetry.ingest.motec_ld import Channel
from fs_telemetry.setups import Setup

FILES = {
    "car.ini": "[BASIC]\nTOTALMASS=1000\n",
    "suspensions.ini": """[BASIC]
CG_LOCATION=0.5
[ARB]
FRONT=10000
REAR=0
[FRONT]
SPRING_RATE=50000
HUB_MASS=40
DAMP_BUMP=3000
DAMP_REBOUND=4500
DAMP_FAST_BUMP=1500
DAMP_FAST_BUMPTHRESHOLD=0.1
DAMP_FAST_REBOUND=2000
DAMP_FAST_REBOUNDTHRESHOLD=0.12
[REAR]
SPRING_RATE=60000
HUB_MASS=40
DAMP_BUMP=3000
DAMP_REBOUND=4500
""",
    "tyres.ini": "[COMPOUND_DEFAULT]\nINDEX=0\n[FRONT]\nRATE=250000\n[REAR]\nRATE=250000\n[FRONT_1]\nRATE=200000\n[REAR_1]\nRATE=200000\n",
    "setup.ini": """[DAMP_BUMP_HF]
SHOW_CLICKS=1
MIN=600
MAX=20000
STEP=100
[DAMP_REBOUND_LR]
SHOW_CLICKS=0
MIN=1000
MAX=9000
STEP=100
[SPRING_RATE_HF]
SHOW_CLICKS=0
MIN=20
MAX=150
STEP=1
""",
}


@pytest.fixture
def car_dir(tmp_path):
    for name, text in FILES.items():
        (tmp_path / name).write_text(text)
    return tmp_path


def quarter(spring, tyre, mass):
    """Ride rate and critical damping, by hand."""
    k = spring * tyre / (spring + tyre)
    return k, 2 * math.sqrt(k * mass)


def test_ratios_from_the_car_defaults(car_dir):
    front, rear = car_setup(car_dir, {}, DamperSetupSettings())
    assert front.sprung_kg == pytest.approx(210)  # 1000 kg × 0.5 / 2 - 40 kg hub
    k, crit = quarter(50000, 250000, 210)
    assert front.critical_ride_ns_m == pytest.approx(crit, abs=1)
    assert front.ride_hz == pytest.approx(math.sqrt(k / 210) / (2 * math.pi), abs=0.01)
    bump = front.dampers[0]
    assert (bump.name, bump.zeta_ride) == ("Bump", pytest.approx(3000 / crit, abs=0.001))
    _, crit_roll = quarter(50000 + 2 * 10000, 250000, 210)  # the bar stiffens roll only
    assert bump.zeta_roll == pytest.approx(3000 / crit_roll, abs=0.001) and front.roll_hz > front.ride_hz
    assert rear.roll_hz == rear.ride_hz  # no rear bar
    assert [r.name for r in rear.dampers] == ["Bump", "Rebound"]  # rear has no fast damping in the files
    assert front.fast_rebound_m_s == 0.12


def test_setup_values_are_read_in_the_setups_units(car_dir):
    # Tatuus-style "HF" (both fronts in one), clicks for bump, N/mm for the spring
    front, rear = car_setup(car_dir, {"DAMP_BUMP_HF": 40, "SPRING_RATE_HF": 80, "DAMP_REBOUND_LR": 5000}, DamperSetupSettings())
    assert front.spring_n_mm == 80 and front.dampers[0].ns_m == 4000 and front.dampers[0].current == "40 clicks"
    assert rear.dampers[1].ns_m == 5000 and rear.dampers[1].current == "5000 N·s/m"


def test_suggestions_hit_the_target_on_the_setups_steps(car_dir):
    front, rear = car_setup(car_dir, {"DAMP_BUMP_HF": 40}, DamperSetupSettings(ls_bump=0.7))
    bump = front.dampers[0]
    assert bump.suggested == f"{round(0.7 * front.critical_ride_ns_m / 100)} clicks"
    assert bump.change == round(0.7 * front.critical_ride_ns_m / 100) - 40
    # A target below the setup's range is clamped to its minimum (1000 N·s/m)
    _, rear = car_setup(car_dir, {}, DamperSetupSettings(ls_rebound=0.01))
    assert rear.dampers[1].suggested == "1000 N·s/m"
    assert front.dampers[1].setup_field is None and front.dampers[1].suggested is None  # not in the setup: can't be set


def test_setup_number_conversions():
    ini = {"X": {"SHOW_CLICKS": "1", "STEP": "250", "MIN": "500", "MAX": "5000"}}
    assert setup_value("X", 8, ini) == 2000 and setup_value("Y", 8, ini) == 8
    assert from_setup("X", 2100, ini) == ("8 clicks", 8.0)
    assert from_setup("X", 99999, ini) == ("20 clicks", 20.0)  # clamped to MAX


def test_a_typed_spring_rate_beats_the_setup(car_dir):
    front, rear = car_setup(car_dir, {"SPRING_RATE_HF": 80}, DamperSetupSettings(spring_front=120))
    assert (front.spring_n_mm, front.spring_from) == (120, "typed")
    assert (rear.spring_n_mm, rear.spring_from) == (60, "car files")
    front, _ = car_setup(car_dir, {"SPRING_RATE_HF": 80}, DamperSetupSettings())
    assert front.spring_from == "setup"
    _, crit = quarter(120000, 250000, 210)
    typed, _ = car_setup(car_dir, {}, DamperSetupSettings(spring_front=120))
    assert typed.critical_ride_ns_m == pytest.approx(crit, abs=1)


def heave_car(car_dir):
    """Tatuus-style: no spring or dampers at the front corners, a heave spring instead."""
    sus = (car_dir / "suspensions.ini").read_text()
    front = sus[sus.index("[FRONT]"):sus.index("[REAR]")]
    heave_front = ("[FRONT]\nSPRING_RATE=0\nHUB_MASS=40\nDAMP_BUMP=0\nDAMP_REBOUND=0\nDAMP_FAST_BUMP=0\nDAMP_FAST_REBOUND=0\n"
                   "[HEAVE_FRONT]\nSPRING_RATE=80000\nDAMP_BUMP=8276\n")
    (car_dir / "suspensions.ini").write_text(sus.replace(front, heave_front))
    return car_dir


def test_heave_car_needs_a_spring_rate(car_dir):
    heave_car(car_dir)
    with pytest.raises(CompareError, match=r"type the front wheel rate.*\[HEAVE_FRONT\] SPRING_RATE 80 N/mm"):
        car_setup(car_dir, {}, DamperSetupSettings())
    front, _ = car_setup(car_dir, {}, DamperSetupSettings(spring_front=80))
    assert front.spring_n_mm == 80 and front.dampers == []  # 0 N·s/m at the corners: no damper rows, not ζ 0
    front, _ = car_setup(car_dir, {"SPRING_RATE_HF": 80, "DAMP_BUMP_HF": 40}, DamperSetupSettings())
    assert front.spring_from == "setup" and [r.name for r in front.dampers] == ["Bump"]


def test_tyre_compound_comes_from_the_setup(car_dir):
    soft, _ = car_setup(car_dir, {"TYRES": 1}, DamperSetupSettings())
    assert soft.tyre_n_mm == 200


def test_load_variation():
    t = np.arange(0, 20, 1 / 30)
    load = 3000 + 300 * np.sin(2 * np.pi * 5 * t)  # 5 Hz wobble: all of it is about the 1 s average
    assert load_variation_pct(load, 30) == pytest.approx(100 * 300 / math.sqrt(2) / 3000, rel=0.03)
    assert load_variation_pct(np.full(600, 3000.0), 30) == 0
    assert load_variation_pct(np.full(10, 3000.0), 30) is None  # too short


class Library:
    def __init__(self, *setups):
        self.setups = {s.id: s for s in setups}

    def get(self, setup_id):
        return self.setups.get(setup_id)


def setup(car="car", **values):
    return Setup(id="s1", name="Race", car=car, track="t", version=1, saved_at="", source="ac", notes="",
                 fingerprint="", values=values)


def with_loads(s):
    t = s.channels["Lap Distance"].data
    for c in ("FL", "FR", "RL", "RR"):
        x = np.arange(len(t)) / 20
        s.channels[f"Tire Load {c}"] = Channel(f"Tire Load {c}", "N", 20, 3000 + 300 * np.sin(2 * np.pi * 4 * x))
    return s


def test_end_to_end(car_dir):
    cat = FakeCatalog(A=with_loads(fake_session()))
    d = compute_damper_setup(cat, [LapRef.parse("A:2")], DamperSetupSettings(setup_id="s1"), Library(setup(DAMP_BUMP_HF=40)), car_dir)
    assert d.setup == "setup 'Race'" and d.missing == "" and d.axles[0].dampers[0].ns_m == 4000
    corner = d.selections[0].corners[0]
    assert corner.load_variation_pct == pytest.approx(7.07, abs=0.3)
    assert corner.fast_bump_pct == 0  # the fake run's suspension doesn't move


def test_wrong_car_setup_and_missing_files(car_dir, tmp_path):
    cat = FakeCatalog(A=with_loads(fake_session()))
    with pytest.raises(CompareError, match="is for other"):
        compute_damper_setup(cat, [LapRef.parse("A:2")], DamperSetupSettings(setup_id="s1"), Library(setup("other")), car_dir)
    d = compute_damper_setup(cat, [LapRef.parse("A:2")], DamperSetupSettings(), Library())
    assert d.axles == [] and "Content Manager" in d.missing and d.selections[0].corners[0].load_variation_pct is not None
