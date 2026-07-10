from __future__ import annotations

import pytest

# pytest-timeout is a hard backstop. It should fire after the soft live-turn
# waiter, leaving enough room for setup/teardown and a clean TimeoutError.
TIMEOUT_BACKSTOP_MARGIN_S = 60


def backstop_timeout(turn_budget_s: int, *, extra_s: int = 0) -> int:
    return turn_budget_s + max(extra_s, TIMEOUT_BACKSTOP_MARGIN_S)


def effective_timeout(item: pytest.Item, turn_budget_s: int) -> int | None:
    """Resolve a pytest-timeout marker against the live-turn budget.

    * no marker, or bare ``@pytest.mark.timeout()`` -> turn budget plus margin;
    * ``@pytest.mark.timeout(extra=n)`` -> turn budget plus max(n, margin);
    * ``@pytest.mark.timeout(n)`` -> leave the absolute timeout untouched.
    """
    marker = item.get_closest_marker("timeout")
    if marker is None:
        return backstop_timeout(turn_budget_s)
    if marker.args:
        return None
    return backstop_timeout(turn_budget_s, extra_s=marker.kwargs.get("extra", 0))
