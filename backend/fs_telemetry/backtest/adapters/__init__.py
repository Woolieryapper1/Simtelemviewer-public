"""Every estimator the backtest checks. To add one: write a module here with an ADAPTER
(see aero_fit.py, and the Adapter class in ../harness.py) and add it to this list."""

from . import aero_fit, balance

ADAPTERS = [
    aero_fit.ADAPTER,
    balance.ADAPTER,
]
