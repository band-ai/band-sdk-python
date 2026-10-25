"""Virtual clock precision compatible with native asyncio timer wakeups."""

from __future__ import annotations

from math import frexp, ldexp
from time import get_clock_info


def binary_clock_resolution() -> float:
    """Keep rounded timer remainders within the host's wakeup tolerance.

    Binary ticks also preserve exact integral timeout budgets.
    """
    _, exponent = frexp(get_clock_info("monotonic").resolution)
    return ldexp(1.0, exponent - 1)
