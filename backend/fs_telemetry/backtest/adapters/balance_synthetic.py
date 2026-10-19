"""Made-up cornering runs with a known balance, for the balance backtest (balance.py).

A simple two-wheel ("bicycle") car drives round a made-up track at the speed its grip allows.
Its tyres are made from a saturating curve, so the car slips more the harder it corners, and
front and rear grip differ so it understeers (or oversteers) by a known amount. From that:

    front wheel angle  =  the angle the corner needs  +  (front slip angle - rear slip angle)

which is exactly the identity the steering-based balance measures rest on (maths.py). So here
the right answer is known at every instant (front minus rear slip angle) and so are the car's
steering ratio and wheelbase. The runs the check uses:

  * three runs of the same car and setup, driven a little differently (different noise and pace)
    -> how far the lap-to-lap numbers move when nothing real has changed
  * copies with ONE known setup change, whose direction is known from the car model
    (stiffer front anti-roll bar = less front grip = more understeer, and so on)
    -> do the measures move the right way, by about the right amount?
  * a car whose steering ratio is another number, and one with a strongly oversteering balance
    -> is the sign right, can the ratio be found?
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from ...ingest.motec_ld import Channel, LdSession
from ..corpus import Run

FREQ = 30  # Hz
G = 9.81
CAR = "synthetic balance car"
LAP_M = 1800.0
LAPS = 6  # an out lap, four timed laps and an unfinished one: a typical short stint
MIN_LAT_G = 0.5  # the app only judges balance above this, so "truth" is averaged over the same moments


@dataclass
class BalanceCar:
    wheelbase_m: float = 1.6
    steering_ratio: float = 5.0  # steering-wheel degrees per degree at the front wheels
    # Each axle: the lateral G at which it runs out of grip, and how many degrees of slip it
    # needs as it nears that limit (a softer axle slips more for the same force).
    limit_front_g: float = 2.2
    limit_rear_g: float = 2.35
    slip_front_deg: float = 4.2
    slip_rear_deg: float = 3.6

    def slip_angles(self, lateral_g: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(front, rear) slip angle in radians when the car corners at this lateral G (>= 0).
        Slip grows faster and faster as the axle nears its limit (a saturating tyre curve)."""
        front = np.arctanh(np.clip(lateral_g / self.limit_front_g, 0, 0.97)) * np.radians(self.slip_front_deg)
        rear = np.arctanh(np.clip(lateral_g / self.limit_rear_g, 0, 0.97)) * np.radians(self.slip_rear_deg)
        return front, rear


def _track(pace: float):
    """Speed, curvature (1/m, + left) and distance along the track, every 1/FREQ s, for LAPS laps."""
    ds = 0.5
    s = np.arange(0, LAP_M, ds)
    # corners: (centre in m, radius in m, length in m, left or right, speed cap in m/s). The gentle
    # bends (capped, so they stay under 0.6 G) are where the steering measure learns its reference.
    corners = [(150, 18, 70, 1, 99), (330, 45, 110, -1, 99), (470, 100, 100, 1, 20), (640, 30, 90, 1, 99),
               (820, 110, 160, -1, 99), (1000, 100, 100, -1, 21), (1150, 22, 80, -1, 99), (1330, 70, 140, 1, 99),
               (1500, 110, 110, 1, 22), (1650, 40, 100, 1, 99)]
    kappa = np.zeros_like(s)
    cap = np.full_like(s, 99.0)
    for centre, radius, length, side, speed_cap in corners:
        kappa += side * np.exp(-0.5 * ((s - centre) / (length / 4)) ** 2) / radius
        cap = np.minimum(cap, np.where(np.abs(s - centre) < length / 2, speed_cap, 99.0))
    # The speed the grip allows (1.75 G in corners, 36 m/s flat out), then brake before and
    # accelerate after the corners: going round the lap twice makes the loop join up.
    v2 = np.minimum(np.minimum(36.0, cap) ** 2, 1.75 * G / np.maximum(np.abs(kappa), 1e-4)) * pace**2
    for _ in range(2):
        for i in range(len(s) * 2):  # accelerating (0.8 G) forwards
            v2[i % len(s)] = min(v2[i % len(s)], v2[i % len(s) - 1] + 2 * 0.8 * G * ds)
        for i in range(len(s) * 2, 0, -1):  # braking (1.5 G) backwards
            v2[i % len(s)] = min(v2[i % len(s)], v2[(i + 1) % len(s)] + 2 * 1.5 * G * ds)
    v_lap = np.sqrt(v2)
    t, speed, k, dist = [], [], [], []
    pos, now = 0.0, 0.0
    while pos < LAP_M * LAPS:
        i = int((pos % LAP_M) / ds) % len(s)
        t.append(now), speed.append(v_lap[i]), k.append(kappa[i]), dist.append(pos)
        now += 1 / FREQ
        pos += v_lap[i] / FREQ
    return np.array(t), np.array(speed), np.array(k), np.array(dist)


def make_session(car: BalanceCar, seed: int, pace: float = 1.0, noise: float = 1.0) -> LdSession:
    rng = np.random.default_rng(seed)
    t, v, kappa, dist = _track(pace)
    n = len(t)
    v_lap_to_lap = 1 + 0.01 * rng.normal(size=LAPS + 1)  # the driver is a little faster or slower each lap
    lap_no = (dist // LAP_M).astype(int)
    v = v * v_lap_to_lap[lap_no]
    ay = v**2 * kappa  # m/s², + left
    direction = np.sign(kappa)
    front, rear = car.slip_angles(np.abs(ay) / G)
    balance = front - rear  # radians: + understeer
    # front wheel angle = angle the path needs + balance, in the direction of the corner
    wheels = car.wheelbase_m * kappa + direction * balance
    steering = np.degrees(wheels) * car.steering_ratio
    yaw_rate = np.degrees(v * kappa)
    wobble = lambda sd: rng.normal(0, sd * noise, n)  # noqa: E731
    slip = lambda a: np.degrees(a)[:, None] * np.array([1, -1, 1, -1]) * direction[:, None] + wobble(0.1)[:, None]  # noqa: E731
    sf, sr = slip(front), slip(rear)

    lat_g = -ay / G + wobble(0.02)  # AC logs lateral G with the opposite sign to yaw rate
    ax = np.gradient(v, t) / G
    brake = np.clip(-ax * 100, 0, 100)
    throttle = np.where(ax > -0.05, 100.0, 0.0) * (np.abs(kappa) < 0.002) + np.where(ax > 0.02, 60.0, 0.0)
    ch = lambda name, unit, data: Channel(name, unit, FREQ, np.asarray(data, float))  # noqa: E731
    channels = [
        ch("Ground Speed", "kph", v * 3.6), ch("Steering Angle", "deg", steering + wobble(0.3)),
        ch("Chassis Yaw Rate", "deg/s", yaw_rate + wobble(0.5)), ch("CG Accel Lateral", "G", lat_g),
        ch("CG Accel Longitudinal", "G", ax), ch("Throttle Pos", "%", np.clip(throttle, 0, 100)), ch("Brake Pos", "%", brake),
        ch("Lap Distance", "m", dist % LAP_M),
        ch("Tire Slip Angle FL", "deg", sf[:, 0]), ch("Tire Slip Angle FR", "deg", sf[:, 1]),
        ch("Tire Slip Angle RL", "deg", sr[:, 0]), ch("Tire Slip Angle RR", "deg", sr[:, 1]),
    ]
    edges = [float(t[np.searchsorted(dist, LAP_M * k)]) for k in range(1, LAPS)]
    return LdSession(path=None, driver="", car=CAR, track="synthetic balance track", date="", time="",
                     channels={c.name: c for c in channels}, beacons=edges,
                     details={"Vehicle Wheelbase": f"{car.wheelbase_m}"})


def truth(car: BalanceCar, pace: float = 1.0) -> dict[str, float]:
    """The right answers for a run: the steering ratio and the front-minus-rear slip angle, averaged
    over the moments the app judges balance (cornering at MIN_LAT_G or more), without any noise."""
    _, v, kappa, _ = _track(pace)
    g = np.abs(v**2 * kappa) / G
    front, rear = car.slip_angles(g)
    cornering = g >= MIN_LAT_G
    return {"steering_ratio": car.steering_ratio, "balance_deg": float(np.degrees(front - rear)[cornering].mean())}


def _run(run_id: str, car: BalanceCar, setup: str, base: str | None = None, seed: int = 1, name: str = CAR,
         **kwargs) -> Run:
    return Run(id=run_id, source="synthetic", car=name, track="synthetic balance track", setup=setup,
               truth=truth(car, kwargs.get("pace", 1.0)), base=base, only_for="balance",
               load=lambda: make_session(car, seed, **kwargs))


def balance_runs() -> list[Run]:
    base = BalanceCar()
    return [
        _run("synthetic-balance-baseline-1", base, "baseline", seed=1),
        _run("synthetic-balance-baseline-2", base, "baseline", seed=2, pace=0.97),
        _run("synthetic-balance-baseline-3", base, "baseline", seed=3, pace=1.02),
        # one change each, with the direction the car model says it must move the balance
        _run("synthetic-balance-stiffer-front-arb", replace(base, limit_front_g=2.1, slip_front_deg=4.5),
             "stiffer front ARB", base="synthetic-balance-baseline-1", seed=1),
        _run("synthetic-balance-stiffer-rear-arb", replace(base, limit_rear_g=2.25, slip_rear_deg=3.9),
             "stiffer rear ARB", base="synthetic-balance-baseline-1", seed=1),
        _run("synthetic-balance-softer-front-arb", replace(base, limit_front_g=2.3, slip_front_deg=3.9),
             "softer front ARB", base="synthetic-balance-baseline-1", seed=1),
        _run("synthetic-balance-softer-rear-arb", replace(base, limit_rear_g=2.45, slip_rear_deg=3.3),
             "softer rear ARB", base="synthetic-balance-baseline-1", seed=1),
        # other cars: a different steering ratio, and one that oversteers
        _run("synthetic-balance-slow-rack", replace(base, steering_ratio=8.0), "slow steering rack", seed=4,
             name=CAR + " (slow rack)"),
        _run("synthetic-balance-oversteery", replace(base, limit_front_g=2.5, slip_front_deg=3.4, limit_rear_g=2.1,
                                                     slip_rear_deg=4.3), "oversteery", seed=5,
             name=CAR + " (oversteery)"),
    ]
