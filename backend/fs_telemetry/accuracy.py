"""How far to trust a balance number, in one plain sentence, from the backtest's accepted numbers.

The Metrics page shows balance measured one of three ways (metrics.BALANCE_MEASURES). The balance
backtest (backtest/adapters/balance.py) checks the two inferred ones against the real slip angles
on AC runs and writes what it found into backend/fs_telemetry/backtest/baseline.json. This reads those
numbers back, so the sentence shown in the app is always what the last accepted backtest said.

The baseline is shipped with the package so accuracy notes work outside a source checkout.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

BASELINE = Path(__file__).with_name("backtest") / "baseline.json"
MIN_LAPS = 10  # a car needs this many laps in the check before it gets a sentence of its own


@lru_cache(maxsize=1)
def _load() -> dict:
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8")).get("balance", {})
    except (OSError, ValueError):
        return {}


def _get(numbers: dict, name: str, car: str) -> float | None:
    value = numbers.get(f"{name} [{car}]")
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else value


def _checked_cars(ac: dict) -> list[str]:
    """The AC cars with enough laps in the check."""
    return sorted(k[len("laps ["):-1] for k, v in ac.items() if k.startswith("laps [") and v and v >= MIN_LAPS)


def _range(values: list[float], digits: int = 2) -> str:
    low, high = min(values), max(values)
    return f"{low:.{digits}f}" if round(low, digits) == round(high, digits) else f"{low:.{digits}f}-{high:.{digits}f}"


def _spread_sentence(ac: dict, car: str, measure: str) -> str:
    """Lap-to-lap movement in one run, as a smallest change worth believing."""
    sd = _get(ac, f"noise_sd_{measure}_deg", car)
    if sd is None:
        return ""
    return (f" Lap to lap in one run it moves about ±{1.96 * sd:.1f}°, so a change under "
            f"{1.96 * sd * math.sqrt(2 / 3):.1f}° between two 3-lap averages is noise.")


def balance_statement(measure: str | None, car: str, baseline: dict | None = None) -> str | None:
    """measure: a key of metrics.BALANCE_MEASURES. baseline: the "balance" part of baseline.json
    (the real file when None)."""
    data = _load() if baseline is None else baseline
    ac = data.get("ac", {}).get("metrics", {})
    if measure is None or not ac:
        return None
    if measure == "slip":
        # the real thing; all that can be said is how much it moves by chance
        return ("Measured from the logged slip angles." + _spread_sentence(ac, car, "slip")) if car in _checked_cars(ac) \
            else "Measured from the logged slip angles."
    if measure not in ("understeer", "steering"):
        return None
    checked = _checked_cars(ac)
    here = car if car in checked else None
    cars = [here] if here else checked
    # A car that isn't one of the AC cars is an iRacing car, where both measures come down to the
    # steering one: what a corner needs is learned from the run, not worked out from the wheelbase.
    which = measure if here else "steering"
    rs = [r for c in cars if (r := _get(ac, f"r_{which}", c)) is not None]
    biases = [b for c in cars if (b := _get(ac, f"bias_{which}_deg", c)) is not None]
    if not rs:
        return None
    laps = sum(_get(ac, "laps", c) or 0 for c in cars)
    whose = "AC runs of this car" if here else f"AC runs of the {len(cars)} cars tested (this car hasn't been checked)"
    text = f"Tracks slip-angle balance with r = {_range(rs)} on {whose}, {laps:.0f} laps."
    # an offset matters when it's as big as the balance itself
    if biases and max(abs(b) for b in biases) >= 0.3:
        low = _range([abs(b) for b in biases], 1)
        text += (f" Reads about {low}° {'low' if sum(biases) < 0 else 'high'} (its zero is the car's gentle-corner "
                 f"behaviour), so compare laps and setups with it rather than reading the sign.")
    trust = [t for c in cars if (t := _get(ac, f"trust_lat_g_{which}", c)) is not None]
    if trust:
        text += f" Moments below about {max(trust):.1f} G of cornering agree much less; lap averages are fine."
    if here:
        text += _spread_sentence(ac, here, which)
    iracing = data.get("iracing", {}).get("metrics", {})
    k_spread = _get(iracing, "k_cv_pct", car)
    if k_spread is not None and not here:
        text += f" The steering reference it learns varied ±{k_spread:.0f}% between iRacing runs of this car."
    return text
