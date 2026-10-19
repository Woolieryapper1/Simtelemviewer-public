"""Damper velocity histograms: where each damper spends its time.

A histogram counts how much of the time a damper moves at each speed. Its
shape shows whether the damper works in the range it was tuned for, and two
histograms side by side show what a click change really did.

Sign convention (the same for every sim and every chart):
    +  bump     - the damper is compressing (suspension travel rising)
    -  rebound  - the damper is extending (suspension travel falling)
iRacing logs damper velocity like that. AC doesn't log it at all, so maths.py
works it out from suspension travel ("Damper Velocity FL (est.)") - and as AC
only logs travel 30 times a second, that reads low on fast events (kerbs).

Speed bands, on each side of zero:
    low   |v| <  low_mm_s   (default 25 mm/s): body roll and pitch from the
                            driver's steering, braking and throttle. This is
                            what the low-speed clicks control.
    mid   in between
    high  |v| >= high_mm_s  (default 100 mm/s): the road hitting the wheel -
                            bumps and kerbs. What the high-speed clicks control.
100 mm/s for "high" because body movements from the driver's inputs rarely get
that fast, while kerb and bump strikes are well beyond it; it's also about
where many race dampers' high-speed (blow-off) circuits take over. Both limits
are settings, so they can match the actual damper.

Every sample of a damper channel is the same slice of time (1 / sample rate),
so counting samples is counting time: all percentages are % of time.

What's counted:
    scope "lap"  each compared lap on its own
    scope "run"  every full timed lap of each compared lap's run (out lap, in
                 lap and pit stops left out, as the pit lane would swamp it)
    gate         optionally only the parts of the lap in one Metrics gate
                 (Braking / LS / MS / HS / Power limited). The gates come from
                 the reference lap by distance, exactly like the Metrics page,
                 so every lap is judged over the same bits of track.

Besides the four corners, the same numbers are worked out for:
    Front / Rear / Left / Right   both dampers of that axle / side together
    Front heave / Rear heave      average of the two dampers: the axle moving
                                  up and down as a whole (braking, aero, crests)
    Front roll / Rear roll        left minus right: the axle rolling.
                                  + = left side compressing faster than the right
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator

from .catalog import Catalog
from .compare import CompareError, LapRef, load_timed_laps
from .ingest.motec_ld import LdSession
from .laps import lap_infos, resample_lap
from .maths import CORNERS, DAMPER_EST
from .metrics import GATES, NEEDED, STEP_M, MetricSettings, classify

BANDS = ["Rebound high", "Rebound mid", "Rebound low", "Bump low", "Bump mid", "Bump high"]
# Every signal there's a histogram of, and how it's made from the four corners
SIGNALS = ["FL", "FR", "RL", "RR", "Front", "Rear", "Left", "Right", "Front heave", "Rear heave", "Front roll", "Rear roll"]
PAIRS = {"Front": ("FL", "FR"), "Rear": ("RL", "RR"), "Left": ("FL", "RL"), "Right": ("FR", "RR")}
RANGE_PERCENTILE = 99.5  # the x axis reaches this far out; the rarest fastest moments go in the end bins
RANGE_STEP = 50.0  # ...rounded up to a multiple of this (mm/s), at least one step


class DamperSettings(BaseModel):
    """What to count and how to bin it. Sent as query parameters; all optional."""

    scope: Literal["lap", "run"] = Field("lap", description="'lap': each compared lap; 'run': every timed lap of each lap's run")
    gate: Literal["all", "Braking", "LS", "MS", "HS", "Power limited"] = Field(
        "all", description="Only count the parts of the lap in this Metrics gate")
    low_mm_s: float = Field(25, gt=0, description="Damper speeds below this (either way) are low speed")
    high_mm_s: float = Field(100, gt=0, description="Damper speeds at or above this (either way) are high speed")
    bin_mm_s: float = Field(10, ge=1, le=100, description="Width of each histogram bar")

    @model_validator(mode="after")
    def _bands_in_order(self):
        if self.high_mm_s <= self.low_mm_s:
            raise ValueError("The high-speed limit must be above the low-speed limit")
        return self


class DamperStats(BaseModel):
    bands_pct: list[float]  # % of time in each of BANDS
    mean_bump_mm_s: float | None  # average speed while in bump (+)
    mean_rebound_mm_s: float | None  # average speed while in rebound (-, so a negative number)
    peak_bump_mm_s: float | None  # fastest bump (one sample - see the note on sample rate)
    peak_rebound_mm_s: float | None  # fastest rebound (negative)
    bump_rebound_ratio: float | None  # time in bump / time in rebound


class Histogram(BaseModel):
    signal: str  # one of SIGNALS
    counts: list[int]  # samples in each bin
    pct: list[float]  # % of the selection's time in each bin
    stats: DamperStats


class Selection(BaseModel):
    session_id: str
    lap: int | None  # None for a whole run
    laps: list[int]  # the laps counted
    driver: str
    time_s: float  # how much running was counted (after the gate)
    sample_hz: int
    estimated: bool  # damper velocity worked out from suspension travel (AC), not logged
    source: str  # where the damper velocity comes from, in words
    histograms: list[Histogram]  # one per SIGNALS, same order


class Dampers(BaseModel):
    settings: DamperSettings
    edges: list[float]  # bin edges in mm/s, shared by every histogram; the end bins include everything beyond
    bands: list[str]
    signals: list[str]
    selections: list[Selection]
    note: str  # caveats worth showing (estimated velocity, sample rate), "" if none


# --- The maths, on plain arrays (easy to test) -------------------------------------


def band_index(v: np.ndarray, low: float, high: float) -> np.ndarray:
    """Which of BANDS each velocity is in. 0 counts as bump (low)."""
    a = np.abs(v)
    size = np.where(a < low, 0, np.where(a < high, 1, 2))  # 0 low, 1 mid, 2 high
    return np.where(v >= 0, 3 + size, 2 - size)


def damper_stats(v: np.ndarray, low: float, high: float) -> DamperStats:
    n = len(v)
    if not n:
        return DamperStats(bands_pct=[0.0] * len(BANDS), mean_bump_mm_s=None, mean_rebound_mm_s=None,
                           peak_bump_mm_s=None, peak_rebound_mm_s=None, bump_rebound_ratio=None)
    counts = np.bincount(band_index(v, low, high), minlength=len(BANDS))
    bump, rebound = v[v > 0], v[v < 0]
    r1 = lambda x: round(float(x), 1)
    return DamperStats(
        bands_pct=[round(100.0 * c / n, 2) for c in counts],
        mean_bump_mm_s=r1(bump.mean()) if len(bump) else None,
        mean_rebound_mm_s=r1(rebound.mean()) if len(rebound) else None,
        peak_bump_mm_s=r1(bump.max()) if len(bump) else None,
        peak_rebound_mm_s=r1(rebound.min()) if len(rebound) else None,
        bump_rebound_ratio=round(len(bump) / len(rebound), 3) if len(rebound) else None,
    )


def histogram_edges(values: list[np.ndarray], bin_mm_s: float) -> np.ndarray:
    """Bin edges, the same either side of zero and with an edge AT zero (so bump
    and rebound never share a bar), reaching out to the 99.5th percentile of speed."""
    allv = np.abs(np.concatenate(values)) if values else np.array([])
    reach = float(np.percentile(allv, RANGE_PERCENTILE)) if len(allv) else 0.0
    half = max(RANGE_STEP, np.ceil(reach / RANGE_STEP) * RANGE_STEP)
    half = np.ceil(half / bin_mm_s) * bin_mm_s  # a whole number of bins each side
    n = int(round(half / bin_mm_s))
    return np.arange(-n, n + 1) * bin_mm_s


def histogram(v: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Samples per bin; anything beyond the ends goes in the end bins (so the bars add up to 100%)."""
    inside = np.clip(v, edges[0], edges[-1])
    counts, _ = np.histogram(inside, bins=edges)
    return counts


def signals(corners: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Every signal in SIGNALS from the four corners' damper velocities (all the same length)."""
    out = {c: corners[c] for c in CORNERS}
    for name, (a, b) in PAIRS.items():
        out[name] = np.concatenate([corners[a], corners[b]])
    for axle, (left, right) in (("Front", ("FL", "FR")), ("Rear", ("RL", "RR"))):
        out[f"{axle} heave"] = (corners[left] + corners[right]) / 2
        out[f"{axle} roll"] = corners[left] - corners[right]
    return out


# --- Picking the samples out of the sessions ---------------------------------------


def damper_channels(s: LdSession) -> dict[str, str]:
    """Corner -> the damper velocity channel to use: logged if there is one, else
    the estimate from suspension travel. Says which channel is missing if neither."""
    out = {}
    for c in CORNERS:
        logged, est = f"Damper Velocity {c}", DAMPER_EST.format(c)
        if logged in s.channels:
            out[c] = logged
        elif est in s.channels:
            out[c] = est
        else:
            raise CompareError(f"{s.driver}'s session has no damper velocity for {c}: it needs a '{logged}' "
                               f"channel, or 'Suspension Travel {c}' to work it out from")
    return out


def _lap_samples(s: LdSession, lap_number: int, names: dict[str, str], gate_at, gate: int | None) -> dict[str, np.ndarray]:
    """The four corners' damper velocity during one lap, on the FL channel's clock,
    keeping only the samples in `gate` (all of them if gate is None)."""
    lap = s.laps[lap_number - 1]
    clock = s.channels[names["FL"]]
    m = (clock.time >= lap.start) & (clock.time < lap.end)
    t = clock.time[m]
    out = {c: np.interp(t, s.channels[n].time, s.channels[n].data) for c, n in names.items()}
    if gate is not None:
        # Each sample's distance round the lap, via its time into the lap (as compare.py does for racing lines)
        grid = resample_lap(s, lap_number, [], STEP_M)
        if len(grid["Distance"]) < 2:
            return {c: v[:0] for c, v in out.items()}
        keep = gate_at(np.interp(t - lap.start, grid["Time"], grid["Distance"])) == gate
        out = {c: v[keep] for c, v in out.items()}
    return out


def _selections(catalog: Catalog, refs: list[LapRef], scope: str) -> list[tuple[LapRef, LdSession, list[int]]]:
    """(the compared lap, its session, the laps to count) for each selection."""
    loaded = load_timed_laps(catalog, refs)  # checks the laps exist, are timed, and share a track
    if scope == "lap":
        return [(ref, s, [ref.lap]) for ref, s, _ in loaded]
    out, seen = [], set()
    for ref, s, _ in loaded:  # one selection per run, in the order the laps were compared
        if ref.session_id in seen:
            continue
        seen.add(ref.session_id)
        out.append((ref, s, [l.number for l in lap_infos(s) if l.kind == "timed"]))
    return out


def compute_dampers(catalog: Catalog, refs: list[LapRef], st: DamperSettings, gating: MetricSettings) -> Dampers:
    chosen = _selections(catalog, refs, st.scope)
    names = {ref.session_id: damper_channels(s) for ref, s, _ in chosen}

    gate, gate_at = None, None
    if st.gate != "all":
        # Gates from the reference lap, by distance - the same as the Metrics page
        ref, ref_s = chosen[0][0], chosen[0][1]
        missing = [c for c in NEEDED if c not in ref_s.channels]
        if missing:
            raise CompareError(f"Can't pick out gates: the reference lap has no {', '.join(missing)} channel(s)")
        r = resample_lap(ref_s, ref.lap, NEEDED, STEP_M)
        ref_gate = classify(r["Ground Speed"], r["Throttle Pos"], r["Brake Pos"], r["CG Accel Lateral"], gating)
        gate = GATES.index(st.gate)

        def gate_at(distance: np.ndarray) -> np.ndarray:
            return ref_gate[np.clip(np.searchsorted(r["Distance"], distance), 0, len(ref_gate) - 1)]

    # Every selection's samples first, so all the histograms can share one set of bins
    picked = []
    for ref, s, laps in chosen:
        laps_samples = [_lap_samples(s, n, names[ref.session_id], gate_at, gate) for n in laps]
        corners = {c: np.concatenate([x[c] for x in laps_samples]) for c in CORNERS}
        picked.append(signals(corners))
    edges = histogram_edges([sig[c] for sig in picked for c in CORNERS], st.bin_mm_s)

    selections, estimated_from = [], {}  # source -> drivers, to say it once
    for (ref, s, laps), sig in zip(chosen, picked):
        ch = s.channels[names[ref.session_id]["FL"]]
        estimated = ch.maths
        if estimated:
            source = f"estimated from {ch.freq} Hz suspension travel (reads low on fast events)"
            estimated_from.setdefault(source, {})[s.driver] = None
        else:
            source = f"logged at {ch.freq} Hz"
        hists = []
        for name in SIGNALS:
            v = sig[name]
            counts = histogram(v, edges)
            total = max(int(counts.sum()), 1)
            hists.append(Histogram(signal=name, counts=[int(c) for c in counts],
                                   pct=[round(100.0 * c / total, 3) for c in counts],
                                   stats=damper_stats(v, st.low_mm_s, st.high_mm_s)))
        selections.append(Selection(
            session_id=ref.session_id, lap=ref.lap if st.scope == "lap" else None, laps=laps, driver=s.driver,
            time_s=round(len(sig["FL"]) / ch.freq, 2), sample_hz=ch.freq, estimated=estimated, source=source,
            histograms=hists,
        ))

    return Dampers(settings=st, edges=[round(float(e), 3) for e in edges], bands=BANDS, signals=SIGNALS,
                   selections=selections,
                   note=" ".join(f"Damper velocity isn't logged in {', '.join(drivers)}'s run{'s' if len(drivers) > 1 else ''}, "
                                 f"so it's {source}." for source, drivers in estimated_from.items()))
