"""Backtest adapter for the balance (understeer / oversteer) measures in metrics.py and maths.py.

The Metrics page shows balance as one of three measures (BALANCE_MEASURES):
    slip         front minus rear slip angle. Logged by AC; the real thing.
    understeer   "Understeer Angle": front wheel angle beyond what the corner needs, from a steering ratio.
    steering     "Steering Understeer": the same in steering-wheel degrees, all learned from the run.
iRacing logs no slip angles, so there the last two are inferences. AC logs the real slip
angles, so on AC runs the inferences can be checked against the truth, lap by lap. What is checked:

  1. Agreement with slip (AC): per car, over timed laps and over each gate (LS / MS / HS / ...):
     correlation, ranking, bias. The measures are in different units (steering is in steering-wheel
     degrees, about 5-15x bigger than wheel degrees), so steering is divided by the car's steering
     ratio first. For "understeer" the ratio is the known one, or the car's typical fitted one:
     the fit the app does for a single run uses the same run's slip angles, which would be cheating.
  2. Sign and scale across speed and lateral G: a straight-line fit of each measure against slip,
     by band, using every 2 m point. Slope 1 = the same degrees, positive = same direction.
  3. The learned steering ratio: how steady it is, how far the learned "gentle corner" ratio is
     from the fitted one, and how many corners it needs before it settles.
  4. Setup changes: made-up runs with one known change (stiffer front anti-roll bar = more understeer
     and so on) must move the measures the right way. AC files don't record the setup, so real AC
     runs can't be sorted by setup unless corpus.json labels them.
  5. Noise: how much a lap's balance moves lap to lap in the same run (same setup and driver) - which
     gives the smallest change worth acting on.
  6. Oversteer stats (share of time oversteering, 5th percentile): agreement with slip, steadiness
     over short stretches, and how much they depend on the lateral-G threshold.
iRacing runs: only the learned ratio can be checked (how steady it is between runs of one car, and
against the ratio in the setup where there is one), and the lap-to-lap noise of the steering measure.

How it works: measure() reads one run and writes lots of small numbers (the keys say what each is:
"c3.1.u.slip" = lap 3, gate 1, mean slip balance), summarise() pools them per car and works out the
statistics. The numbers are kept as plain floats so the harness can cache them for real files.
"""

from __future__ import annotations

import numpy as np

from ... import maths
from ... import metrics as app
from ...cars import car_facts
from ...ingest.motec_ld import Channel
from ...laps import lap_infos, resample_lap
from .. import metrics as m
from ..corpus import Run
from ..harness import Adapter, Check, Measured, column, perturbation_pairs
from . import balance_synthetic

NAN = float("nan")
MIN_LAT_G = 0.5  # the app's default balance_min_lat_g
THRESHOLDS = (0.3, 0.5, 0.7, 1.0)  # lateral G thresholds for the oversteer stats
MEAN_THRESHOLDS = (0.5, 0.8, 1.1, 1.4)  # ...and for the lap means: does a higher threshold agree better?
WINDOWS = (50, 150, 500)  # points (2 m each) in a "short run" window: 100 m, 300 m, 1 km
CORNER_COUNTS = (1, 2, 3, 5, 8, 12, 20)  # corners a ratio fit is given, to see when it settles
SPEED_BANDS = (("slow", 0, 80), ("mid", 80, 160), ("fast", 160, 1e9))  # kph, like the LS / MS / HS gates
LAT_BANDS = ((0.3, 0.5), (0.5, 0.8), (0.8, 1.1), (1.1, 1.5), (1.5, 99))
GATE_NAMES = {0: "braking", 1: "ls", 2: "ms", 3: "hs", 4: "power"}
MIN_POINTS = 25  # a lap (or lap and gate) needs this many 2 m points of cornering to count
MIN_BAND_POINTS = 200  # ...and a speed or lateral-G band this many points, to be fitted
MIN_LAPS = 10  # a car needs this many laps to get an accuracy statement
MIN_RUNS = 5  # ...and this many runs for a spread between runs to mean anything
GOOD_R = 0.8  # "tracks well" = correlation of at least this
INPUTS = ["Steering Angle", "Chassis Yaw Rate", "Ground Speed", "CG Accel Lateral", "Brake Pos"]


# --- Reading one run ----------------------------------------------------------------------------


def _add(s, name: str, data: np.ndarray, freq: int) -> None:
    s.channels[name] = Channel(name, "", freq, np.asarray(data, float))


def _test_channels(s, wheelbase: float | None) -> bool:
    """Add the channels the check works from; True if the steering measure could be learned.

    Understeer Angle is  steering / ratio - path,  so for any ratio it is  a / ratio - b  with
    a = steering angle in the corner's direction and b = the angle the path needs. Keeping a and b
    apart lets summarise() try any steering ratio without reading the run again."""
    freq, x = maths._on_one_clock([s.channels[i] for i in INPUTS])
    st = maths.steering_understeer(x)
    if wheelbase:
        v = x["Ground Speed"] / 3.6
        path = np.degrees(wheelbase * np.radians(x["Chassis Yaw Rate"]) / np.maximum(v, maths.MIN_SPEED))
        ok = (v > maths.MIN_SPEED) & (np.abs(path) > 0.5)  # the same test as maths.understeer_angle
        _add(s, "bt a", np.where(ok, x["Steering Angle"] * np.sign(path), 0.0), freq)
        _add(s, "bt b", np.where(ok, np.abs(path), 0.0), freq)
        _add(s, "bt ok", ok, freq)
    if st is not None:
        _add(s, "bt st", st, freq)
        _add(s, "bt st ok", st != 0, freq)
    return st is not None


def _stats(x: np.ndarray, y: np.ndarray, prefix: str, out: dict) -> None:
    """The sums that give a correlation and a straight-line fit of y on x, for any group of points."""
    out |= {f"{prefix}.n": len(x), f"{prefix}.sx": x.sum(), f"{prefix}.sy": y.sum(), f"{prefix}.sxx": (x * x).sum(),
            f"{prefix}.syy": (y * y).sum(), f"{prefix}.sxy": (x * y).sum()}


def _share_below_zero(v: np.ndarray) -> float:
    return 100.0 * float(np.mean(v < 0)) if len(v) else NAN


def _fifth_percentile(v: np.ndarray) -> float:
    return float(np.percentile(v, 5)) if len(v) else NAN


def _measure_laps(s, ratio: float | None, has_slip: bool, has_st: bool, out: dict) -> bool:
    """Per timed lap (and per gate): the means the lap-level checks use, the oversteer stats, and
    every 2 m point's contribution to the speed and lateral-G bands. False if there are no timed laps."""
    laps = [i for i in lap_infos(s) if i.kind == "timed"]
    if not laps:
        return False
    names = app.NEEDED + (app.SLIP_ANGLES if has_slip else []) + [c for c in ("bt a", "bt b", "bt ok", "bt st", "bt st ok") if c in s.channels]
    resampled = {i.number: resample_lap(s, i.number, names, app.STEP_M) for i in laps}
    resampled = {n: r for n, r in resampled.items() if len(r["Distance"]) > 10}
    if not resampled:
        return False
    settings = app.MetricSettings()
    ref = resampled[min(resampled)]
    ref_gate = app.classify(ref["Ground Speed"], ref["Throttle Pos"], ref["Brake Pos"], ref["CG Accel Lateral"], settings)

    points = {k: [] for k in ("lap", "lat", "speed", "slip", "us", "st", "u", "s", "gate")}
    for number, r in resampled.items():
        gate = ref_gate[np.clip(np.searchsorted(ref["Distance"], r["Distance"]), 0, len(ref_gate) - 1)]
        lat = np.abs(r["CG Accel Lateral"])
        slip = app.balance(r) if has_slip else np.full(len(lat), NAN)
        have_us = "bt ok" in r
        a = r["bt a"] if have_us else np.full(len(lat), NAN)
        b = r["bt b"] if have_us else np.full(len(lat), NAN)
        u = (r["bt ok"] > 0.999) if have_us else np.zeros(len(lat), bool)  # the steering-ratio measure is measured here
        s_ok = (r["bt st ok"] > 0.999) if has_st else np.zeros(len(lat), bool)  # ...and the learned one here
        us = a / ratio - b if ratio and have_us else np.full(len(lat), NAN)
        st = r["bt st"] if has_st else np.full(len(lat), NAN)
        for k, v in (("lap", np.full(len(lat), number)), ("lat", lat), ("speed", r["Ground Speed"]), ("slip", slip),
                     ("us", us), ("st", st), ("u", u), ("s", s_ok), ("gate", gate)):
            points[k].append(v)
        # lap and gate means: only where the lap is cornering hard enough, like the Metrics page
        for g in ("all", *GATE_NAMES):
            in_gate = np.ones(len(lat), bool) if g == "all" else gate == g
            for kind, valid in (("u", u), ("s", s_ok)):
                mask = valid & in_gate & (lat >= MIN_LAT_G)
                if mask.sum() < MIN_POINTS:
                    continue
                p = f"c{number}.{g}.{kind}"
                out[f"{p}.n"] = int(mask.sum())
                out[f"{p}.lat"] = float(lat[mask].mean())
                out[f"{p}.slip"] = float(slip[mask].mean())
                if kind == "u":
                    out[f"{p}.a"], out[f"{p}.b"] = float(a[mask].mean()), float(b[mask].mean())
                else:
                    out[f"{p}.st"] = float(st[mask].mean())
        for t in MEAN_THRESHOLDS:
            for name, vec, valid in (("slip", slip, u), ("a", a, u), ("b", b, u), ("st", st, s_ok)):
                mask = valid & (lat >= t)
                if mask.sum() >= MIN_POINTS:
                    out[f"m{int(t * 10)}.{number}.{name}"] = float(vec[mask].mean())
        # the oversteer stats, per lap, at each lateral-G threshold
        for t in THRESHOLDS:
            for name, vec, valid in (("slip", slip, u), ("us", us, u), ("st", st, s_ok)):
                mask = valid & (lat >= t)
                if mask.sum() >= MIN_POINTS:
                    out[f"os{int(t * 10)}.{number}.{name}.pct"] = _share_below_zero(vec[mask])
                    out[f"os{int(t * 10)}.{number}.{name}.p5"] = _fifth_percentile(vec[mask])
    p = {k: np.concatenate(v) for k, v in points.items()}

    # Speed and lateral-G bands, every point: how well does each measure follow slip here?
    bands = [(f"speed_{n}", (p["speed"] >= lo) & (p["speed"] < hi) & (p["lat"] >= MIN_LAT_G)) for n, lo, hi in SPEED_BANDS]
    bands += [(f"lat_{lo}", (p["lat"] >= lo) & (p["lat"] < hi)) for lo, hi in LAT_BANDS]
    for band, in_band in bands:
        if has_slip:
            for name, valid in (("us", p["u"]), ("st", p["s"])):
                mask = in_band & valid
                if mask.sum():
                    _stats(p["slip"][mask], p[name][mask], f"pt.{band}.{name}", out)

    # Short stretches: how far does the oversteer stat move on a stretch of N points, compared with the whole run?
    rng = np.random.default_rng(0)
    for name, valid in (("slip", p["u"]), ("us", p["u"]), ("st", p["s"])):
        v = p[name][valid & (p["lat"] >= MIN_LAT_G)]
        for n in WINDOWS:
            if len(v) < 2 * n:
                continue
            starts = rng.integers(0, len(v) - n, 40)
            pct = np.array([_share_below_zero(v[i:i + n]) for i in starts])
            p5 = np.array([_fifth_percentile(v[i:i + n]) for i in starts])
            out[f"win{n}.{name}.pct_dev"] = float(np.sqrt(np.mean((pct - _share_below_zero(v)) ** 2)))
            out[f"win{n}.{name}.p5_dev"] = float(np.sqrt(np.mean((p5 - _fifth_percentile(v)) ** 2)))
    return True


def _ratio_by_corners(s, wheelbase: float | None, out: dict) -> None:
    """The learned steering ratio (needs slip angles) and the gentle-corner K, after only the first few corners."""
    freq, x = maths._on_one_clock([s.channels[i] for i in INPUTS])
    for n in (*CORNER_COUNTS, None):
        tag = "" if n is None else f"_c{n}"
        k = maths.gentle_corner_k(x, n)
        out[f"k{tag}"] = NAN if k is None else k
        if wheelbase and all(c in s.channels for c in app.SLIP_ANGLES):
            fit = maths.fit_steering_ratio(s, wheelbase, n)
            out[f"ratio_fit{tag}"] = NAN if fit is None else fit


def measure(run: Run) -> dict[str, float] | None:
    s = run.session()
    if not all(c in s.channels for c in INPUTS):
        return None
    has_slip = all(c in s.channels for c in app.SLIP_ANGLES)
    facts = car_facts(s)
    wheelbase = facts.wheelbase_m
    has_st = _test_channels(s, wheelbase)
    out: dict[str, float] = {}
    _ratio_by_corners(s, wheelbase, out)
    if not np.isfinite(out["k"]) and not has_slip:
        return None  # iRacing run without enough gentle corners to learn from
    ratio = facts.steering_ratio or out.get("ratio_fit")
    if facts.steering_ratio:
        out["ratio_known"] = facts.steering_ratio
    if ratio is not None and not np.isfinite(ratio):
        ratio = None
    out["ratio_used"] = ratio if ratio else NAN
    out["wheelbase_m"] = wheelbase or NAN
    _measure_laps(s, ratio if has_slip else None, has_slip, has_st, out)  # (a run with no timed laps still tells us its ratio)
    out["has_slip"] = float(has_slip)
    for key, value in run.truth.items():
        out[f"true_{key}"] = value
    return out


# --- Pooling the numbers ----------------------------------------------------------------------


def _pearson(x, y) -> float:
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3 or x[ok].std() == 0 or y[ok].std() == 0:
        return NAN
    return float(np.corrcoef(x[ok], y[ok])[0, 1])


def _band_fit(total: dict[str, float]) -> tuple[float, float, int]:
    """(slope of the measure on slip, correlation, points) from summed sums."""
    n = total.get("n", 0)
    if n < MIN_BAND_POINTS:
        return NAN, NAN, int(n)
    sx, sy, sxx, syy, sxy = (total[k] for k in ("sx", "sy", "sxx", "syy", "sxy"))
    var_x, var_y, cov = sxx / n - (sx / n) ** 2, syy / n - (sy / n) ** 2, sxy / n - sx * sy / n**2
    if var_x <= 0 or var_y <= 0:
        return NAN, NAN, int(n)
    return cov / var_x, cov / np.sqrt(var_x * var_y), int(n)


def _cells(v: dict[str, float]) -> list[dict]:
    """The lap and gate cells of one run: {lap, gate, kind, n, lat, slip, a, b, st}."""
    cells: dict[tuple, dict] = {}
    for key, value in v.items():
        if key[0] != "c" or key.count(".") != 3:
            continue
        lap, gate, kind, field = key[1:].split(".")
        cells.setdefault((int(lap), gate, kind), {"lap": int(lap), "gate": gate, "kind": kind})[field] = value
    return list(cells.values())


def _median(values) -> float:
    values = np.asarray(values, float)
    return float(np.nanmedian(values)) if np.isfinite(values).any() else NAN


def _per_car(ms: list[Measured]) -> dict[str, list[Measured]]:
    cars: dict[str, list[Measured]] = {}
    for x in ms:
        if x.values:
            cars.setdefault(x.run.car, []).append(x)
    return cars


def _ratio_for_car(runs: list[Measured]) -> float:
    """The steering ratio to judge the car by: the known one, else the middle of the fitted ones."""
    known = _median([x.values.get("ratio_known", NAN) for x in runs])
    return known if np.isfinite(known) else _median([x.values.get("ratio_fit", NAN) for x in runs])


def _agreement(car: str, runs: list[Measured], out: dict) -> None:
    """Check 1 and 2 for one car, written into out as "name [car]"."""
    ratio_ref = _ratio_for_car(runs)
    rows = {"u": [], "s": []}
    for x in runs:
        for c in _cells(x.values):
            c["run"], c["ratio_run"] = x.run.id, x.values.get("ratio_used", NAN)
            rows[c["kind"]].append(c)

    def table(kind: str, gate: str, field: str) -> np.ndarray:
        return np.array([c.get(field, NAN) for c in rows[kind] if c["gate"] == gate])

    for gate in ("all", *map(str, GATE_NAMES)):
        label = "" if gate == "all" else f"_{GATE_NAMES[int(gate)]}"
        slip_u, slip_s = table("u", gate, "slip"), table("s", gate, "slip")
        us = table("u", gate, "a") / ratio_ref - table("u", gate, "b")
        st = table("s", gate, "st") / ratio_ref  # steering-wheel degrees to wheel degrees
        if len(slip_u) >= 5:
            out[f"r_understeer{label} [{car}]"] = _pearson(us, slip_u)
        if len(slip_s) >= 5:
            out[f"r_steering{label} [{car}]"] = _pearson(st, slip_s)
        if gate != "all":
            continue
        out[f"laps [{car}]"] = len(slip_u)
        out[f"laps_steering [{car}]"] = len(slip_s)
        for name, est, truth in (("understeer", us, slip_u), ("steering", st, slip_s)):
            if len(truth) < 5:
                continue
            out[f"rho_{name} [{car}]"] = m.spearman(est, truth)
            out[f"pairs_{name} [{car}]"] = 100 * m.pair_agreement(est, truth)
            out[f"bias_{name}_deg [{car}]"] = m.bias(est, truth)
            out[f"rmse_{name}_deg [{car}]"] = m.rmse(est, truth)
            out[f"scale_{name} [{car}]"] = float(np.std(est) / np.std(truth)) if np.std(truth) else NAN
            judged = np.abs(truth) > 0.25  # a lap whose slip balance is about 0 has no sign to get wrong
            out[f"sign_ok_pct_{name} [{car}]"] = 100 * float(np.mean(np.sign(est[judged]) == np.sign(truth[judged]))) if judged.any() else NAN
        out[f"slip_mean_deg [{car}]"] = float(np.mean(slip_u)) if len(slip_u) else NAN
        out[f"slip_sd_deg [{car}]"] = float(np.std(slip_u)) if len(slip_u) else NAN
    # does a higher lateral-G threshold make the lap averages agree better?
    for t in MEAN_THRESHOLDS:
        tag = int(t * 10)
        laps = {name: {} for name in ("slip", "a", "b", "st")}
        for x in runs:
            for key, value in x.values.items():
                if key.startswith(f"m{tag}."):
                    _, lap, name = key.split(".")
                    laps[name][(x.run.id, lap)] = value
        keys = [k for k in laps["slip"] if k in laps["a"] and k in laps["b"]]
        if len(keys) >= 5:
            est = np.array([laps["a"][k] / ratio_ref - laps["b"][k] for k in keys])
            truth = np.array([laps["slip"][k] for k in keys])
            out[f"r_understeer_T{t} [{car}]"], out[f"bias_understeer_deg_T{t} [{car}]"] = _pearson(est, truth), m.bias(est, truth)
        keys = [k for k in laps["slip"] if k in laps["st"]]
        if len(keys) >= 5:
            est = np.array([laps["st"][k] / ratio_ref for k in keys])
            truth = np.array([laps["slip"][k] for k in keys])
            out[f"r_steering_T{t} [{car}]"], out[f"bias_steering_deg_T{t} [{car}]"] = _pearson(est, truth), m.bias(est, truth)
    # the app's own way (ratio fitted on the same run's slip angles): shows how much that flatters it
    pairs = [(c["a"] / c["ratio_run"] - c["b"], c["slip"]) for c in rows["u"] if c["gate"] == "all" and np.isfinite(c["ratio_run"])]
    if len(pairs) >= 5:
        out[f"r_understeer_own_ratio [{car}]"] = _pearson(*zip(*pairs))

    # sign and scale by band: straight-line fit over every point
    for band in [f"speed_{n}" for n, _, _ in SPEED_BANDS] + [f"lat_{lo}" for lo, _ in LAT_BANDS]:
        for name in ("us", "st"):
            total: dict[str, float] = {}
            for x in runs:
                for k in ("n", "sx", "sy", "sxx", "syy", "sxy"):
                    total[k] = total.get(k, 0) + x.values.get(f"pt.{band}.{name}.{k}", 0)
            slope, r, n = _band_fit(total)
            label = "understeer" if name == "us" else "steering"
            out[f"band_slope_{label}_{band} [{car}]"] = slope / ratio_ref if name == "st" else slope
            out[f"band_r_{label}_{band} [{car}]"] = r
    for label in ("understeer", "steering"):
        speed = [out[f"band_slope_{label}_speed_{n} [{car}]"] for n, _, _ in SPEED_BANDS]
        speed = [v for v in speed if np.isfinite(v)]
        out[f"scale_spread_{label} [{car}]"] = max(speed) - min(speed) if len(speed) > 1 else NAN
        # "don't trust below X G": the lowest lateral-G band from which every band up has r >= GOOD_R
        trust = NAN
        for lo, _ in reversed(LAT_BANDS):
            r = out[f"band_r_{label}_lat_{lo} [{car}]"]
            if not np.isfinite(r):
                continue
            if r < GOOD_R:
                break
            trust = lo
        out[f"trust_lat_g_{label} [{car}]"] = trust


def _noise(car: str, runs: list[Measured], out: dict) -> None:
    """Check 5: lap-to-lap spread inside a run (same setup, same driver)."""
    ratio_ref = _ratio_for_car(runs)
    wheel_units = np.isfinite(ratio_ref)  # else (iRacing) the steering measure stays in steering-wheel degrees
    ratio_ref = ratio_ref if wheel_units else 1.0
    spread = {"slip": [], "understeer": [], "steering": []}
    dof = {k: 0 for k in spread}
    for x in runs:
        laps = {"slip": [], "understeer": [], "steering": []}
        for c in _cells(x.values):
            if c["gate"] != "all":
                continue
            if c["kind"] == "u":
                laps["slip"].append(c["slip"])
                laps["understeer"].append(c["a"] / ratio_ref - c["b"])
            else:
                laps["steering"].append(c["st"] / ratio_ref)
        for k, v in laps.items():
            if len(v) >= 2:
                spread[k].append((len(v) - 1) * np.var(v, ddof=1))
                dof[k] += len(v) - 1
    for k in spread:
        if not dof[k]:
            continue  # e.g. no slip angles (iRacing): nothing to say about slip or the ratio measure
        sd = float(np.sqrt(sum(spread[k]) / dof[k]))
        k = k if wheel_units or k != "steering" else "steering_wheel"
        out[f"noise_sd_{k}_deg [{car}]"] = sd
        out[f"ci95_{k}_3laps_deg [{car}]"] = 1.96 * sd / np.sqrt(3)  # the mean of 3 laps is this close to the run's usual balance
        out[f"mdc_{k}_3laps_deg [{car}]"] = 1.96 * sd * np.sqrt(2 / 3)  # smallest change between two 3-lap averages to believe


def _oversteer(car: str, runs: list[Measured], out: dict) -> None:
    """Check 6, the per-car part: the stats against slip, and how they move with the lateral-G threshold."""
    ratio_ref = _ratio_for_car(runs)
    by_name = {"slip": {}, "us": {}, "st": {}}
    for t in THRESHOLDS:
        table = {k: {} for k in by_name}
        for x in runs:
            for key, value in x.values.items():
                if key.startswith(f"os{int(t * 10)}."):
                    _, lap, name, stat = key.split(".")
                    table[name][(x.run.id, lap, stat)] = value
        for name in by_name:
            by_name[name][t] = table[name]
    for t in THRESHOLDS:
        for name, label in (("slip", "slip"), ("us", "understeer"), ("st", "steering")):
            pct = [v for (_, _, stat), v in by_name[name][t].items() if stat == "pct"]
            out[f"oversteer_pct_T{t}_{label} [{car}]"] = float(np.mean(pct)) if pct else NAN
    t = MIN_LAT_G
    for name, label in (("us", "understeer"), ("st", "steering")):
        for stat in ("pct", "p5"):
            keys = [k for k in by_name[name][t] if k[2] == stat and k in by_name["slip"][t]]
            if len(keys) < 5:
                continue
            est = np.array([by_name[name][t][k] for k in keys])
            truth = np.array([by_name["slip"][t][k] for k in keys])
            if stat == "p5" and name == "st":
                est = est / ratio_ref
            out[f"os_{stat}_r_{label} [{car}]"] = _pearson(est, truth)
            out[f"os_{stat}_bias_{label} [{car}]"] = m.bias(est, truth)
    # how much the share of oversteer in the slip balance itself depends on the threshold
    pcts = [out[f"oversteer_pct_T{t}_slip [{car}]"] for t in THRESHOLDS]
    out[f"oversteer_pct_threshold_range_slip [{car}]"] = float(np.nanmax(pcts) - np.nanmin(pcts)) if np.isfinite(pcts).any() else NAN


def _ratio_quality(car: str, runs: list[Measured], out: dict) -> None:
    """Check 3: the learned steering ratio and the gentle-corner K."""
    fits = column(runs, "ratio_fit")
    ks = column(runs, "k")
    fit_ref = _median(fits)
    if np.isfinite(fits).sum() >= 2:
        out[f"ratio_fit_median [{car}]"] = fit_ref
        out[f"ratio_fit_cv_pct [{car}]"] = 100 * float(np.nanstd(fits, ddof=1)) / fit_ref
    known = _median([x.values.get("ratio_known", x.values.get("true_steering_ratio", NAN)) for x in runs])
    if np.isfinite(known):
        out[f"ratio_fit_error_vs_known_pct [{car}]"] = 100 * (fit_ref - known) / known
    # the gentle-corner K divided by the wheelbase is a steering ratio too: how far from the fitted one?
    if np.isfinite(ks).any():
        out[f"k_cv_pct [{car}]"] = 100 * float(np.nanstd(ks, ddof=1)) / float(np.nanmean(ks)) if np.isfinite(ks).sum() > 1 else NAN
        wb = _median(column(runs, "wheelbase_m"))
        if np.isfinite(wb) and np.isfinite(fit_ref):
            out[f"k_ratio_vs_fit_pct [{car}]"] = 100 * (float(np.nanmedian(ks)) / wb - fit_ref) / fit_ref
    # corners needed: error of the fit after n corners, against the fit from the whole run
    for what, full in (("ratio_fit", "ratio_fit"), ("k", "k")):
        settled = NAN
        for n in CORNER_COUNTS:
            after, whole = column(runs, f"{what}_c{n}"), column(runs, full)
            have = np.isfinite(after)
            err = 100 * np.abs(after[have] / whole[have] - 1) if have.any() else np.array([NAN])
            out[f"{what}_after_{n}_corners_have_pct [{car}]"] = 100 * float(have.mean())
            out[f"{what}_after_{n}_corners_error_pct [{car}]"] = float(np.nanmedian(err)) if have.any() else NAN
            if not np.isfinite(settled) and have.mean() >= 0.8 and np.nanmedian(err) <= 5:
                settled = n
        out[f"{what}_corners_to_settle [{car}]"] = settled


def summarise(ms: list[Measured]) -> dict[str, float]:
    out: dict[str, float] = {}
    cars = _per_car(ms)
    for car, runs in cars.items():
        slip_runs = [x for x in runs if x.values.get("has_slip")]
        if slip_runs:
            _agreement(car, slip_runs, out)
            _oversteer(car, slip_runs, out)
            _ratio_quality(car, slip_runs, out)
        _noise(car, runs if not slip_runs else slip_runs, out)
        if not slip_runs:
            _gentle_k(car, runs, out)

    # worst car (with enough laps), for the gate
    def worst(name: str, best: str = "min") -> float:
        values = [v for k, v in out.items() if k.startswith(name + " [") and np.isfinite(v)
                  and out.get(f"laps [{k.split(' [', 1)[1]}", 0) >= MIN_LAPS]
        return (min if best == "min" else max)(values) if values else NAN

    out["lap_r_understeer_worst_car"] = worst("r_understeer")
    out["lap_r_steering_worst_car"] = worst("r_steering")
    out["ratio_fit_cv_pct_worst_car"] = worst("ratio_fit_cv_pct", "max")
    out["noise_sd_understeer_deg_worst_car"] = worst("noise_sd_understeer_deg", "max")
    out["scale_spread_understeer_worst_car"] = worst("scale_spread_understeer", "max")
    # iRacing (no slip angles): the learned reference between runs of one car and setup
    same_setup = [v for k, v in out.items() if k.startswith("k_cv_pct_same_setup [") and np.isfinite(v)]
    out["k_cv_pct_same_setup_worst_car"] = max(same_setup) if same_setup else NAN
    # windows: how far the oversteer stats of a short stretch are from the whole run's
    for n in WINDOWS:
        for name in ("slip", "us", "st"):
            for stat in ("pct", "p5"):
                out[f"win{n}_{stat}_dev_{name}"] = _median(column(ms, f"win{n}.{name}.{stat}_dev"))

    pairs = perturbation_pairs(ms)
    if pairs:
        # The made-up cars have a known steering ratio, which is how iRacing cars are read when the
        # ratio is known; "(fitted)" is how the app reads an AC car it has no files for (ratio fitted
        # on each run's own slip angles, which soaks up part of a real change in balance).
        readings = {"slip": _lap_mean_slip, "understeer": lambda x: _lap_mean_us(x, "true_steering_ratio"),
                    "steering": lambda x: _lap_mean_st(x, "true_steering_ratio"),
                    "understeer (fitted)": lambda x: _lap_mean_us(x, "ratio_used")}
        right, sizes = {k: [] for k in readings}, {k: [] for k in readings}
        for a, b in pairs:
            expected = b.values["true_balance_deg"] - a.values["true_balance_deg"]
            for label, getter in readings.items():
                move = m.moved_as_expected(getter(a), getter(b), expected)
                right[label].append(move["right_way"])
                sizes[label].append(abs(move["size_ratio"] - 1) * 100)
                out[f"perturbation_size_ratio_{label} [{b.run.setup}]"] = move["size_ratio"]
        for label in readings:
            tag = label.replace(" (fitted)", "_fitted")
            out[f"perturbation_right_way_pct_{tag}"] = 100 * float(np.mean(right[label]))
            out[f"perturbation_size_error_pct_{tag}"] = float(np.nanmax(sizes[label]))
        out["perturbation_right_way_pct"] = min(out[f"perturbation_right_way_pct_{k}"] for k in ("slip", "understeer", "steering"))
    return out


def _gentle_k(car: str, runs: list[Measured], out: dict) -> None:
    """iRacing (no slip angles): how steady the learned K is between runs, and what it says about the car."""
    ks = column(runs, "k")
    if np.isfinite(ks).sum() >= MIN_RUNS:
        out[f"k_cv_pct [{car}]"] = 100 * float(np.nanstd(ks, ddof=1)) / float(np.nanmean(ks))
        out[f"k_median [{car}]"] = float(np.nanmedian(ks))
    groups = m.spread_by_group([x.run.group for x in runs], ks)  # same car AND setup
    cvs = [g["cv_pct"] for g in groups.values() if g["runs"] >= 3 and np.isfinite(g["cv_pct"])]
    if cvs:
        out[f"k_cv_pct_same_setup [{car}]"] = float(np.median(cvs))
    known = column(runs, "ratio_known")
    if np.isfinite(known).any():
        out[f"wheelbase_implied_m [{car}]"] = _median(ks / known)
    for n in CORNER_COUNTS:
        after = column(runs, f"k_c{n}")
        have = np.isfinite(after)
        out[f"k_after_{n}_corners_have_pct [{car}]"] = 100 * float(have.mean())
        out[f"k_after_{n}_corners_error_pct [{car}]"] = float(np.nanmedian(100 * np.abs(after[have] / ks[have] - 1))) if have.any() else NAN
    settled = NAN
    for n in CORNER_COUNTS:
        if (out[f"k_after_{n}_corners_have_pct [{car}]"] >= 80
                and out[f"k_after_{n}_corners_error_pct [{car}]"] <= 5):
            settled = n
            break
    out[f"k_corners_to_settle [{car}]"] = settled


def _lap_values(x: Measured, kind: str, field) -> list[float]:
    return [field(c) for c in _cells(x.values) if c["gate"] == "all" and c["kind"] == kind]


def _lap_mean_slip(x: Measured) -> float:
    return float(np.mean(_lap_values(x, "u", lambda c: c["slip"])))


def _lap_mean_us(x: Measured, ratio_key: str) -> float:
    ratio = x.values[ratio_key]
    return float(np.mean(_lap_values(x, "u", lambda c: c["a"] / ratio - c["b"])))


def _lap_mean_st(x: Measured, ratio_key: str) -> float:
    ratio = x.values[ratio_key]
    return float(np.mean(_lap_values(x, "s", lambda c: c["st"] / ratio)))


ADAPTER = Adapter(
    name="balance",
    title="Balance: understeer / oversteer measures - metrics.py, maths.py",
    sources=("synthetic", "ac", "iracing"),
    measure=measure,
    summarise=summarise,
    synthetic=balance_synthetic.balance_runs,
    notes=("AC: Understeer Angle and Steering Understeer against the logged slip-angle balance, lap by lap, per car. "
           "Steering is converted to wheel degrees with the car's steering ratio so all three are in the same unit. "
           "Made-up runs with one known setup change check the direction. iRacing: only the learned ratio and the "
           "lap-to-lap noise can be checked."),
    checks=[
        Check("lap_r_understeer_worst_car", "higher", tolerance=0.05),
        Check("lap_r_steering_worst_car", "higher", tolerance=0.05),
        Check("ratio_fit_cv_pct_worst_car", "lower", tolerance=1.0),
        Check("noise_sd_understeer_deg_worst_car", "lower", tolerance=0.1),
        Check("scale_spread_understeer_worst_car", "lower", tolerance=0.15),
        Check("k_cv_pct_same_setup_worst_car", "lower", tolerance=3.0),
        Check("perturbation_right_way_pct", "higher", tolerance=0.0, limit=100.0),
        Check("perturbation_size_error_pct_understeer", "lower", tolerance=10.0),
        Check("perturbation_size_error_pct_steering", "lower", tolerance=10.0),
    ],
)
