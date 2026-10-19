"""Maths channels: extra channels calculated from the logged ones.

Like MoTeC i2 maths channels, but worked out once here so every page (and
every CSV export) gets them. They're added to a session when it's loaded and
behave exactly like logged channels, just flagged `maths=True`.

How a maths channel is calculated:
  1. Its inputs may be logged at different rates (e.g. 20 Hz and 30 Hz), so
     first put them all on one clock - the fastest input's rate.
  2. Apply the formula to those arrays.

To add one: write a function taking {input name: array} and returning an array,
and add a MathChannel to CHANNELS. If an input is missing from a file, that
maths channel is simply skipped for that file. Channels that also need facts
about the car (steering ratio, wheelbase) go in car_channels, using cars.py.

Sign conventions, checked against real MFT02 data:
  * AC's lateral G and yaw rate have OPPOSITE signs (correlation -0.94), so the
    yaw rate the cornering force "asks for" is  -latG * g / speed.
  * Slip ratio is + when a wheel spins faster than the road (wheelspin) and
    - when slower (locking under braking).
  * Brake torque split matches the setup's brake bias (65.1% vs FRONT_BIAS=65).
  * Suspension travel rises as the suspension compresses (it moves opposite to
    ride height), so damper velocity is + in bump and - in rebound. iRacing's
    logged damper velocity has the same sign (correlation 0.99 with d(travel)/dt).

iRacing files (ingest/ibt.py) arrive with AC channel names, so the same formulas
work on them. iRacing doesn't log slip ratios, but it does log wheel speeds, so
those are ESTIMATED here (flagged approximate, as is anything calculated from them).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .aero import add_aero_channels
from .cars import car_facts
from .ingest.motec_ld import Channel, LdSession

G = 9.81
PEDAL_ON = 2.0  # % - a pedal above this counts as pressed
MIN_SPEED = 5.0  # m/s - below this, yaw maths is meaningless (parked, pit lane)
HALF_TRACK_M = 0.8  # assumed half the track width: most cars are 1.5-1.7 m wide at the wheels, and the .ibt doesn't say
CORNERS = ("FL", "FR", "RL", "RR")


@dataclass
class MathChannel:
    name: str
    unit: str
    description: str
    inputs: list[str]
    formula: Callable[[dict[str, np.ndarray]], np.ndarray]
    approximate: bool = False  # an estimate, not an exact calculation (anything using an estimate is one too)
    only_without: tuple[str, ...] = ()  # skipped for files that have any of these (they have a better measure)
    basis: str = ""  # what the numbers rest on: known facts about the car, or something learned from the run


# --- Formulas -------------------------------------------------------------------


def overlap(x):
    """How much throttle and brake are pressed at the same time (the smaller pedal)."""
    thr, brk = x["Throttle Pos"], x["Brake Pos"]
    return np.where((thr > PEDAL_ON) & (brk > PEDAL_ON), np.minimum(thr, brk), 0.0)


def coasting(x):
    return ((x["Throttle Pos"] <= PEDAL_ON) & (x["Brake Pos"] <= PEDAL_ON)).astype(float)


def g_absolute(x):
    return np.hypot(x["CG Accel Lateral"], x["CG Accel Longitudinal"])


def slip_ratio_from_wheel_speed(corner):
    """Slip ratio (%) from how fast the wheel turns vs how fast the ground under it moves.

    In a corner the outside wheels cover more ground than the inside ones, so a
    wheel's ground speed is the car's speed +/- yaw rate x half the track width.
    Without that, the outside wheels would look like they're spinning in every
    corner. The track width is assumed (HALF_TRACK_M); 0.1 m off is about 0.4%
    of slip in a slow hairpin. Yaw rate is + turning left, as iRacing logs it
    (only iRacing files have "Wheel Speed" channels)."""
    outside_when_turning_left = 1.0 if corner[1] == "R" else -1.0

    def formula(x):
        v = x["Ground Speed"] / 3.6
        ground = v + outside_when_turning_left * np.radians(x["Chassis Yaw Rate"]) * HALF_TRACK_M
        wheel = x[f"Wheel Speed {corner}"] / 3.6
        return np.where(v > MIN_SPEED, 100.0 * (wheel - ground) / np.maximum(ground, 1.0), 0.0)

    return formula


def rear_wheelspin(x):
    return np.clip(np.maximum(x["Tire Slip Ratio RL"], x["Tire Slip Ratio RR"]), 0, None)


def front_lockup(x):
    return np.clip(-np.minimum(x["Tire Slip Ratio FL"], x["Tire Slip Ratio FR"]), 0, None)


def slip_balance(x):
    front = (np.abs(x["Tire Slip Angle FL"]) + np.abs(x["Tire Slip Angle FR"])) / 2
    rear = (np.abs(x["Tire Slip Angle RL"]) + np.abs(x["Tire Slip Angle RR"])) / 2
    return front - rear


def yaw_excess(x):
    """Measured yaw rate minus the yaw rate the cornering force asks for, in the
    direction of the corner: + rotating more than the path (oversteer),
    - rotating less (understeer). 0 when not really cornering."""
    v = x["Ground Speed"] / 3.6
    expected = np.degrees(-x["CG Accel Lateral"] * G / np.maximum(v, MIN_SPEED))
    measured = x["Chassis Yaw Rate"]
    excess = (measured - expected) * np.sign(expected)
    return np.where((v > MIN_SPEED) & (np.abs(expected) > 2.0), excess, 0.0)


def understeer_angle(steering_ratio, wheelbase_m):
    """How much more the front wheels are steered than the car's path needs, in
    degrees at the wheels: + understeer, - oversteer, 0 when not really cornering.

    The path needs  wheelbase x yaw rate / speed  of steering (radians). That's
    the angle a car with no tyre slip would need for the same corner. The front
    wheels are actually at  steering-wheel angle / steering ratio. In theory
    the difference equals front minus rear slip angle (Balance Slip Angle), so
    this works on files without slip angles (iRacing). Steering and yaw rate
    are both + turning left, in AC and iRacing alike."""

    def formula(x):
        v = x["Ground Speed"] / 3.6
        wheels = x["Steering Angle"] / steering_ratio
        path = np.degrees(wheelbase_m * np.radians(x["Chassis Yaw Rate"]) / np.maximum(v, MIN_SPEED))
        # Measured in the direction of the corner, so + means understeer turning either way
        return np.where((v > MIN_SPEED) & (np.abs(path) > 0.5), (wheels - path) * np.sign(path), 0.0)

    return formula


def first_corners(use: np.ndarray, corners: int | None) -> np.ndarray:
    """Keep only the first `corners` separate stretches of True in `use` (None = all of them).
    Lets the backtest ask "how many corners does a fit need before it settles?"."""
    if corners is None:
        return use
    start = use & ~np.concatenate([[False], use[:-1]])  # the first sample of each stretch
    return use & (np.cumsum(start) <= corners)


def gentle_corner_k(x, corners: int | None = None) -> float | None:
    """Steering-wheel degrees per degree of heading change per metre (= steering ratio x wheelbase),
    learned from the gentle corners of a run, where the tyres barely slip. None if there are too few.

    Not quite: the tyres do slip a little even here, and a car that understeers needs more lock than
    the path alone says, so this reads high (the backtest found 27% above the slip-fitted ratio on
    the MFT02, 9% on a Mustang). Drawing a line through speed bands down to zero speed removed most of
    that on AC but made K jump around more from run to run on iRacing, so the plain median stays. The
    effect is a steady offset in Steering Understeer, which is why it is good for comparing laps and
    setups and not for reading the sign of the balance."""
    v = x["Ground Speed"] / 3.6
    path = np.degrees(np.radians(x["Chassis Yaw Rate"]) / np.maximum(v, MIN_SPEED))  # heading change, deg per metre
    steer, lat = x["Steering Angle"], np.abs(x["CG Accel Lateral"])
    gentle = ((v > 6) & (v < 35) & (np.abs(path) > 0.3) & (lat > 0.1) & (lat < 0.6) & (x["Brake Pos"] < 5)
              & (np.sign(steer) == np.sign(path)))
    gentle = first_corners(gentle, corners)
    if gentle.sum() < 60:  # about a second of it
        return None
    return float(np.median(steer[gentle] / path[gentle]))


def steering_understeer(x):
    """Steering beyond what the corner needs, in STEERING-WHEEL degrees, in the
    direction of the corner: + understeer (more lock than the path needs),
    - oversteer (less, or countersteer). 0 when not really cornering.

    A car whose tyres didn't slip would need  K x yaw rate / speed  of steering
    for its path, where K = steering ratio x wheelbase. iRacing gives neither,
    but K shows in the run itself: in slow, gentle corners the tyres barely
    slip, so there steering / (yaw rate / speed) is K. Checked on 218 iRacing
    runs (backtest/adapters/balance.py): K varies about 5-20% (typically 12%) between
    runs of one car. And it reads a little high, so the whole channel sits about
    1 degree low on AC cars where the slip angles show it. Read it for trends and
    lap-vs-lap differences, not exact degrees or the sign. Anything
    more than K x yaw rate / speed while cornering harder is understeer.
    Steering and yaw rate are both + turning left.
    Returns None (no channel) if the run has too few gentle corners to measure K."""
    k = gentle_corner_k(x)
    if k is None:
        return None
    v = x["Ground Speed"] / 3.6
    path = np.degrees(np.radians(x["Chassis Yaw Rate"]) / np.maximum(v, MIN_SPEED))
    steer = x["Steering Angle"]
    needed = k * path
    return np.where((v > MIN_SPEED) & (np.abs(needed) > 5.0), (steer - needed) * np.sign(needed), 0.0)


MECH_MIN_LAT_G = 0.5  # cornering at least this hard, so plenty of load is moving across
STEADY_LAT_G_PER_S = 1.0  # lateral G changing slower than this: settled in the corner, not turning in or out
STEADY_MAX_LONG_G = 0.3  # and not braking or accelerating hard
LEVEL_G = 0.05  # |lateral G| below this (and |longitudinal G| below 2x) counts as level running, for the static offset


def mech_balance(x):
    """Mechanical balance (ESTIMATED): the front's share of the load moving across
    the car in a corner (lateral load transfer distribution, LLTD), %. Set by the
    springs, anti-roll bars and roll centres. More at the front means the fronts
    lose grip first: an understeer tendency. Only in steady cornering (see
    load_transfer); 0 otherwise. Needs tyre loads, so AC only.

    Checked on real runs (see test_metrics.py): MFT02 42.6-45.2% across 26
    runs, Ford Mustang Supercar 51.3-55.0%. Run to run it follows the front/rear
    roll stiffness split seen in the suspension travel: front travelling less
    per G relative to the rear (a stiffer front) goes with a higher front share."""
    front, total, steady = load_transfer(x, MECH_MIN_LAT_G)
    return np.where(steady, 100.0 * front / np.where(steady, total, 1.0), 0.0)


def load_transfer(x, min_lat_g: float = MECH_MIN_LAT_G):
    """Load moving across the car (N, + towards the left): at the front axle, in
    total (front + rear), and which samples are steady cornering, fit to judge it.

    Left minus right tyre load on each axle. Downforce pushes both sides down
    equally, so it cancels out here: no aero correction is needed (AC's aero
    acts on the car's centre line). What's left over at rest - fuel, the driver
    sitting off-centre - is measured on the level bits of the run (straights,
    pits) and taken off, so left and right corners agree.

    Steady cornering: at least min_lat_g, lateral G changing slower than
    STEADY_LAT_G_PER_S and not braking/accelerating hard. Turn-in and exit are
    left out because there the dampers, not the springs and bars, decide where
    the load goes. x needs "Time" (s) for the rate of change."""
    lat, lon = x["CG Accel Lateral"], x["CG Accel Longitudinal"]
    front = x["Tire Load FL"] - x["Tire Load FR"]
    rear = x["Tire Load RL"] - x["Tire Load RR"]
    level = (np.abs(lat) < LEVEL_G) & (np.abs(lon) < 2 * LEVEL_G)
    if level.sum() >= 20:
        front, rear = front - np.median(front[level]), rear - np.median(rear[level])
    total = front + rear
    with np.errstate(divide="ignore", invalid="ignore"):  # a repeated time (parked) gives no rate: not steady
        rate = np.abs(np.gradient(lat, x["Time"])) if len(lat) > 1 else np.zeros_like(lat)
    steady = ((np.abs(lat) >= min_lat_g) & (rate < STEADY_LAT_G_PER_S) & (np.abs(lon) < STEADY_MAX_LONG_G)
              & (np.abs(total) > 1.0))
    return front, total, steady


def brake_balance(x):
    front = x["Brake Torque FL"] + x["Brake Torque FR"]
    total = front + x["Brake Torque RL"] + x["Brake Torque RR"]
    # Only meaningful while braking; 0 otherwise (never "not a number", which can't be sent as JSON)
    return np.where(total > 50.0, 100.0 * front / np.maximum(total, 1e-9), 0.0)


def rake(x):
    return x["Ride Height Rear"] - x["Ride Height Front"]


def temp_spread(corner):
    return lambda x: x[f"Tire Temp Inner {corner}"] - x[f"Tire Temp Outer {corner}"]


def damper_velocity(corner, freq, to_mm):
    """How fast the damper is moving (mm/s): the rate of change of suspension travel.
    + bump (compressing), - rebound (extending): travel rises as the suspension
    compresses in AC and iRacing alike (it falls as ride height falls).

    A central difference (the change from the sample before to the sample after,
    over 2 samples) is used. Spanning two samples makes it a light filter in
    itself; no more smoothing is added, as on iRacing runs (which log the real
    damper velocity) every extra smoothing step made it read even lower."""
    def formula(x):
        travel = x[f"Suspension Travel {corner}"] * to_mm
        return np.gradient(travel, 1.0 / freq) if len(travel) > 1 else np.zeros_like(travel)

    return formula


# --- The list --------------------------------------------------------------------

CHANNELS: list[MathChannel] = [
    # Estimates for files that log wheel speeds but not slip (iRacing). Files that
    # log slip ratio (AC) keep their own, as maths never overwrites a logged channel.
    *[MathChannel(f"Tire Slip Ratio {c}", "%", f"ESTIMATED from wheel speed vs ground speed, {c}: + spinning, - locking. "
                  "Assumes a 1.6 m track width, so treat small values (a few %) as indicative.",
                  ["Wheel Speed " + c, "Ground Speed", "Chassis Yaw Rate"], slip_ratio_from_wheel_speed(c), approximate=True)
      for c in CORNERS],
    MathChannel("Throttle Brake Overlap", "%", "Smaller pedal while both throttle and brake are pressed; 0 otherwise.",
                ["Throttle Pos", "Brake Pos"], overlap),
    MathChannel("Coasting", "", "1 while neither pedal is pressed, 0 otherwise.",
                ["Throttle Pos", "Brake Pos"], coasting),
    MathChannel("G Absolute", "G", "Total grip in use: √(lateral² + longitudinal²) - also called combined G or G sum.",
                ["CG Accel Lateral", "CG Accel Longitudinal"], g_absolute),
    MathChannel("Rear Wheelspin", "%", "Biggest rear slip ratio under power (0 when not spinning).",
                ["Tire Slip Ratio RL", "Tire Slip Ratio RR"], rear_wheelspin),
    MathChannel("Front Lockup", "%", "Biggest front slip ratio under braking, as a positive number (0 when not locking).",
                ["Tire Slip Ratio FL", "Tire Slip Ratio FR"], front_lockup),
    MathChannel("Balance Slip Angle", "deg", "Average front minus rear slip angle. + understeer, − oversteer.",
                [f"Tire Slip Angle {c}" for c in CORNERS], slip_balance),
    MathChannel("Yaw Rate Excess", "deg/s", "Yaw rate beyond what the cornering force asks for. + oversteer (rotating more than the path), − understeer.",
                ["Ground Speed", "CG Accel Lateral", "Chassis Yaw Rate"], yaw_excess),
    # For files without slip angles whose steering ratio isn't known (else Understeer Angle does it, see car_channels)
    MathChannel("Steering Understeer", "deg", "ESTIMATED steering-wheel angle beyond what the corner needs. "
                "+ understeer, − oversteer.",
                ["Steering Angle", "Chassis Yaw Rate", "Ground Speed", "CG Accel Lateral", "Brake Pos"], steering_understeer,
                approximate=True, only_without=("Tire Slip Angle FL", "Understeer Angle"),
                basis="learned from this run's gentle corners; steering ratio unknown"),
    MathChannel("Mech Balance (est.)", "% front", "ESTIMATED mechanical balance: front share of the load moving "
                "across the car in steady cornering (lateral load transfer distribution, LLTD). More front = "
                "understeer tendency. 0 when not settled in a corner (below 0.5 G, turning in or out, braking hard).",
                [f"Tire Load {c}" for c in CORNERS] + ["CG Accel Lateral", "CG Accel Longitudinal"], mech_balance,
                approximate=True, basis="logged tyre loads (AC only), steady cornering"),
    MathChannel("Brake Balance Actual", "%", "Share of brake torque at the front while braking; 0 when not braking.",
                [f"Brake Torque {c}" for c in CORNERS], brake_balance),
    MathChannel("Rake", "mm", "Rear minus front ride height.",
                ["Ride Height Rear", "Ride Height Front"], rake),
    *[MathChannel(f"Tire Temp Spread {c}", "C", f"Inner minus outer tread temperature, {c}. + inner hotter (more camber effect).",
                  [f"Tire Temp Inner {c}", f"Tire Temp Outer {c}"], temp_spread(c)) for c in CORNERS],
]


def _on_one_clock(channels: list[Channel]) -> tuple[int, dict[str, np.ndarray]]:
    """All inputs resampled onto the fastest input's clock, plus that clock as "Time" (s)."""
    freq = max(ch.freq for ch in channels)
    duration = max(len(ch.data) / ch.freq for ch in channels)
    t = np.arange(int(round(duration * freq))) / freq
    return freq, {"Time": t, **{ch.name: np.interp(t, ch.time, ch.data) for ch in channels}}


def fit_steering_ratio(s: LdSession, wheelbase_m: float, corners: int | None = None) -> float | None:
    """Work out the steering ratio from the run itself, for cars whose ratio isn't known.

    In a corner, front wheel angle = the angle the path needs + front minus rear
    slip angle. AC logs slip angles, so the ratio is whatever makes the steering
    wheel angle fit that best. On the MFT02 this gives 5.0-5.6 run to run.
    None if the run hasn't got the channels or enough proper cornering. `corners` limits the
    fit to the first few corners (only the backtest uses that)."""
    inputs = ["Steering Angle", "Chassis Yaw Rate", "Ground Speed", *[f"Tire Slip Angle {c}" for c in CORNERS]]
    if not all(i in s.channels for i in inputs):
        return None
    _, x = _on_one_clock([s.channels[i] for i in inputs])
    v = x["Ground Speed"] / 3.6
    path = np.degrees(wheelbase_m * np.radians(x["Chassis Yaw Rate"]) / np.maximum(v, MIN_SPEED))
    steering = x["Steering Angle"]
    # Properly cornering, and steering into the corner (countersteering would spoil the fit)
    use = first_corners((v > 10) & (np.abs(path) > 1.0) & (np.sign(steering) == np.sign(path)), corners)
    if use.sum() < 300:  # 10 s at 30 Hz
        return None
    wheels = np.abs(path[use]) + slip_balance(x)[use]
    steering = np.abs(steering[use])
    ratio = np.sum(steering**2) / np.sum(steering * wheels)  # best fit of  steering / ratio = wheels
    return round(float(ratio), 2) if 2 < ratio < 30 else None  # outside that, the run is no good for this


UNDERSTEER_DESC = "Front wheel angle minus the angle the car's path needs. + understeer, − oversteer."


def car_channels(s: LdSession) -> list[MathChannel]:
    """Understeer Angle, using what's KNOWN about the car first and what can be
    LEARNED from the run otherwise. The channel's basis says which:
      1. steering ratio and wheelbase both known (cars.py, the car's AC files,
         the .ldx, the iRacing setup) -> exact maths, not an estimate
      2. wheelbase known, ratio not (AC): ratio fitted to the run's slip angles
      3. ratio known, wheelbase not (iRacing): the steering a corner needs is
         learned from the run's gentle corners (see steering_understeer)
      4. neither: no Understeer Angle. Files without slip angles get Steering
         Understeer instead (all learned, in steering-wheel degrees)."""
    facts = car_facts(s)
    ratio, wheelbase = facts.steering_ratio, facts.wheelbase_m
    inputs = ["Steering Angle", "Chassis Yaw Rate", "Ground Speed"]
    if ratio and wheelbase:
        basis = f"known steering ratio {ratio}:1 ({facts.ratio_from}) and wheelbase {wheelbase} m ({facts.wheelbase_from})"
        return [MathChannel("Understeer Angle", "deg", UNDERSTEER_DESC, inputs, understeer_angle(ratio, wheelbase),
                            basis=basis)]
    if wheelbase and (fitted := fit_steering_ratio(s, wheelbase)):
        basis = (f"steering ratio {fitted}:1 learned from this run's slip angles, "
                 f"known wheelbase {wheelbase} m ({facts.wheelbase_from})")
        return [MathChannel("Understeer Angle", "deg", UNDERSTEER_DESC, inputs, understeer_angle(fitted, wheelbase),
                            approximate=True, basis=basis)]
    if ratio:
        basis = (f"known steering ratio {ratio}:1 ({facts.ratio_from}); what a corner needs learned "
                 f"from this run's gentle corners, as the wheelbase is unknown")

        def at_the_wheels(x):
            extra_steering = steering_understeer(x)
            return None if extra_steering is None else extra_steering / ratio

        return [MathChannel("Understeer Angle", "deg", UNDERSTEER_DESC, inputs + ["CG Accel Lateral", "Brake Pos"],
                            at_the_wheels, approximate=True, basis=basis)]
    return []


DAMPER_EST = "Damper Velocity {} (est.)"  # what dampers.py looks for when a file has no logged damper velocity


def damper_channels(s: LdSession) -> list[MathChannel]:
    """Damper velocity worked out from suspension travel, for files that don't
    log it (AC / telemetrick exports don't; iRacing does, so it keeps its own).

    It's an ESTIMATE that reads LOW on fast movements: AC logs travel only 30
    times a second, and a kerb strike is over in a few hundredths of a second,
    so the samples miss most of the peak. Checked on iRacing runs, which log both
    travel and the real damper velocity: from travel at 30 Hz, typical speeds
    read about 10-15% low, the fastest 1% about 15-35% low and the single
    highest peak half or less. The low-speed range (body roll and pitch from the
    driver's inputs) is much slower, so it comes out close to right."""
    out = []
    for c in CORNERS:
        travel = s.channels.get(f"Suspension Travel {c}")
        if travel is None:
            continue
        to_mm = 1000.0 if travel.unit == "m" else 1.0  # telemetrick and our iRacing reader both give mm
        desc = (f"ESTIMATED damper speed {c}, worked out from suspension travel logged at {travel.freq} Hz: "
                f"+ bump (compressing), − rebound (extending). At {travel.freq} Hz fast events (kerbs, bumps) "
                "are under-read: typical speeds read ~10-15% low, the fastest 1% ~15-35% low, peaks half or less "
                "(checked against iRacing's logged damper velocity). Slow body movements come out close to right.")
        out.append(MathChannel(DAMPER_EST.format(c), "mm/s", desc, [travel.name],
                               damper_velocity(c, travel.freq, to_mm), approximate=True,
                               only_without=(f"Damper Velocity {c}",),
                               basis=f"derived from {travel.freq} Hz suspension travel"))
    return out


def _add(s: LdSession, mc: MathChannel) -> None:
    if (mc.name in s.channels or not all(i in s.channels for i in mc.inputs)
            or any(n in s.channels for n in mc.only_without)):
        return  # never overwrite a logged channel; skip if an input is missing
    freq, x = _on_one_clock([s.channels[i] for i in mc.inputs])
    values = mc.formula(x)
    if values is None:
        return  # the formula can't work on this run (e.g. not enough of the running it needs)
    values = np.nan_to_num(np.asarray(values, dtype=float))
    approximate = mc.approximate or any(s.channels[i].approximate for i in mc.inputs)
    description = f"{mc.description} Based on: {mc.basis}." if mc.basis else mc.description
    s.channels[mc.name] = Channel(mc.name, mc.unit, freq, values, maths=True, approximate=approximate,
                                  description=description, basis=mc.basis)


def add_maths_channels(s: LdSession) -> LdSession:
    """Add every maths channel whose inputs this session has. Returns the same session."""
    for mc in car_channels(s) + damper_channels(s) + CHANNELS:  # car channels first: Understeer Angle replaces Steering Understeer
        _add(s, mc)
    add_aero_channels(s)  # aero needs a per-run calibration, so it lives in app/aero.py
    return s
