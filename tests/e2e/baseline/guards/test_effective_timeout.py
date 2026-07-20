"""Guard for the baseline pytest-timeout backstop resolution.

``effective_timeout`` sets each baseline test's hard pytest-timeout. That backstop
is a SIGALRM that skips async unwinding and can poison the process, so it must sit a
margin ABOVE the soft reply barrier (= the turn budget): the barrier raises a clean
TimeoutError first, and the backstop only ever catches a genuine hang. This pins that
invariant across the marker forms ``effective_timeout`` understands.
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.toolkit.timeouts import (
    TIMEOUT_BACKSTOP_MARGIN_S,
    effective_timeout,
)

BASE = 120


def _item(marker: object | None) -> SimpleNamespace:
    """A stand-in pytest item exposing only the ``get_closest_marker`` the SUT uses."""
    return SimpleNamespace(get_closest_marker=lambda _name: marker)


def _marker(*args: object, **kwargs: object) -> SimpleNamespace:
    """A stand-in pytest marker (``.args`` / ``.kwargs`` are all the SUT reads)."""
    return SimpleNamespace(args=args, kwargs=kwargs)


def test_no_marker_adds_the_backstop_margin_over_the_turn_budget() -> None:
    # The default every unmarked test gets: the backstop clears the barrier (= BASE).
    assert effective_timeout(_item(None), BASE) == BASE + TIMEOUT_BACKSTOP_MARGIN_S


def test_extra_beyond_the_margin_is_honored() -> None:
    # A slow-framework test asks for real headroom; it is added to the turn budget.
    assert effective_timeout(_item(_marker(extra=480)), BASE) == BASE + 480


def test_extra_below_the_margin_never_drops_under_the_backstop_floor() -> None:
    # Even a tiny extra keeps the full margin, so the backstop can't race the barrier.
    assert (
        effective_timeout(_item(_marker(extra=5)), BASE)
        == BASE + TIMEOUT_BACKSTOP_MARGIN_S
    )


def test_absolute_timeout_is_left_to_pytest_timeout_natively() -> None:
    # Positional @pytest.mark.timeout(n): honored natively, not rewritten.
    assert effective_timeout(_item(_marker(600)), BASE) is None
