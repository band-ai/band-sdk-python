"""Guard for the baseline pytest-timeout backstop resolution.

The pytest-timeout backstop is a hard process interrupt, so it must sit above
the soft reply barrier: the barrier raises a clean TimeoutError first, and the
backstop only catches a genuine hang. This pins that invariant across the marker
forms the baseline collection hook understands.
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.e2e.baseline.conftest import _effective_timeout
from tests.e2e.baseline.settings import BaselineSettings


def _settings() -> BaselineSettings:
    return BaselineSettings(e2e_timeout=120, e2e_timeout_backstop_margin=60)


def _item(marker: object | None) -> SimpleNamespace:
    """A stand-in pytest item exposing only the ``get_closest_marker`` the SUT uses."""
    return SimpleNamespace(get_closest_marker=lambda _name: marker)


def _marker(*args: object, **kwargs: object) -> SimpleNamespace:
    """A stand-in pytest marker (``.args`` / ``.kwargs`` are all the SUT reads)."""
    return SimpleNamespace(args=args, kwargs=kwargs)


def test_no_marker_adds_the_backstop_margin_over_the_turn_budget() -> None:
    settings = _settings()
    assert (
        _effective_timeout(_item(None), settings)
        == settings.e2e_default_backstop_timeout()
    )


def test_extra_beyond_the_margin_is_honored() -> None:
    settings = _settings()
    assert _effective_timeout(
        _item(_marker(extra=480)), settings
    ) == settings.e2e_default_backstop_timeout(extra=480)


def test_extra_below_the_margin_never_drops_under_the_backstop_floor() -> None:
    settings = _settings()
    assert _effective_timeout(
        _item(_marker(extra=5)), settings
    ) == settings.e2e_default_backstop_timeout(extra=5)


def test_absolute_timeout_is_left_to_pytest_timeout_natively() -> None:
    # Positional @pytest.mark.timeout(n): honored natively, not rewritten.
    assert _effective_timeout(_item(_marker(600)), _settings()) is None
