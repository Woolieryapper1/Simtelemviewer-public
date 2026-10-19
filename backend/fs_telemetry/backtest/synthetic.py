"""Made-up runs with a known answer, so the backtest always has something to check.

The car is invented, and its tyre loads are made from the same equations aero.py
assumes (see the top of aero.py), plus noise, bumps and (on one run) an "off" where
the loads go wild. So the right answer - the ClA, aero balance and weight that went
in - is known exactly, and stored in the run's `truth`.

The runs, for the different kinds of check:
  * three runs of the same car and setup, driven differently and with different noise
    -> stability: they should give the same answer
  * "more front wing" and "less rear wing": copies of the first run with one aero change
    -> perturbation: the estimate should move the way the change does, by about as much

To check another estimator on synthetic data, add the channels it needs to make_session
(or write a new maker) and the answers it should find to the run's truth.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..ingest.motec_ld import Channel, LdSession
from .corpus import Run

FREQ = 30  # Hz, like AC's tyre loads
RHO = 1.2  # kg/m³
CAR = "synthetic car"


@dataclass
class Car:
    weight_n: float = 3000.0  # car + driver
    front: float = 0.45  # static weight on the front axle
    k: float = -500.0  # N of front load per G of longitudinal acceleration (= -weight x CG height / wheelbase)
    cla_front: float = 1.3  # m²
    cla_rear: float = 2.5  # m²

    def truth(self) -> dict[str, float]:
        cla = self.cla_front + self.cla_rear
        return {"cla_m2": cla, "balance_front_pct": 100 * self.cla_front / cla,
                "mass_kg": self.weight_n / 9.81, "static_front_pct": 100 * self.front}


def make_session(car: Car, seed: int, minutes: float = 5, speed_cycle_s: float = 60, off: bool = False) -> LdSession:
    """A run where each lap is one slow-fast-slow speed cycle, with bumps and noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, minutes * 60, 1 / FREQ)
    v = 17.5 - 17.5 * np.cos(2 * np.pi * t / speed_cycle_s)  # m/s, 0-35
    ax = np.gradient(v, t) / 9.81  # G
    az = 0.05 * np.sin(t * 7) + rng.normal(0, 0.01, len(t))
    lat = 0.6 * np.sin(2 * np.pi * t / 9) * (v > 8)  # some cornering, which the aero fit should leave out
    q = 0.5 * RHO * v**2
    total = car.weight_n * (1 + az) + (car.cla_front + car.cla_rear) * q + rng.normal(0, 20, len(t))
    front = car.weight_n * car.front * (1 + az) + car.k * ax + car.cla_front * q + rng.normal(0, 10, len(t))
    if off:  # a 10 s "off" in the second lap: loads all over the place
        bad = (t > 70) & (t < 80)
        total[bad] *= rng.uniform(0.2, 2.5, bad.sum())
        front[bad] *= rng.uniform(0.2, 2.5, bad.sum())
    rear = total - front
    ch = lambda name, unit, data: Channel(name, unit, FREQ, np.asarray(data, float))  # noqa: E731
    channels = [
        ch("Tire Load FL", "N", front / 2), ch("Tire Load FR", "N", front / 2),
        ch("Tire Load RL", "N", rear / 2), ch("Tire Load RR", "N", rear / 2),
        ch("CG Accel Vertical", "G", az), ch("CG Accel Longitudinal", "G", ax), ch("CG Accel Lateral", "G", lat),
        ch("Ground Speed", "kph", v * 3.6), ch("Air Density", "kg/m3", np.full_like(t, RHO)),
    ]
    return LdSession(path=None, driver="", car=CAR, track="synthetic track", date="", time="",
                     channels={c.name: c for c in channels},
                     beacons=list(np.arange(speed_cycle_s, t[-1], speed_cycle_s)),  # a lap per speed cycle
                     details={"Vehicle Wheelbase": "1.5", "Vehicle Weight": f"{car.weight_n / 9.81:.1f}",
                              "Vehicle COG": f"{car.front}"})


def _run(run_id: str, car: Car, setup: str, base: str | None = None, **kwargs) -> Run:
    return Run(id=run_id, source="synthetic", car=CAR, track="synthetic track", setup=setup, truth=car.truth(),
               base=base, load=lambda: make_session(car, **kwargs))


def synthetic_runs() -> list[Run]:
    base = Car()
    return [
        _run("synthetic-baseline-1", base, "baseline", seed=1),
        _run("synthetic-baseline-2", base, "baseline", seed=2, speed_cycle_s=50),
        _run("synthetic-baseline-with-off", base, "baseline", seed=3, off=True),
        _run("synthetic-more-front-wing", Car(cla_front=1.6), "more front wing", base="synthetic-baseline-1", seed=1),
        _run("synthetic-less-rear-wing", Car(cla_rear=2.1), "less rear wing", base="synthetic-baseline-1", seed=1),
    ]
