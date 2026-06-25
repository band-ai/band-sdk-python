"""Declarative test-dependency requirements.

``@requires(Dep.OPENAI, ...)`` declares the *optional* capabilities a test
needs; it attaches a marker resolved by a hook in conftest.py. The always-on
gate (E2E enabled, Band keys present) is applied to every baseline test by that
hook — it is not a dependency you pass here.

Optional deps skip when absent (the run just can't exercise them). Add one by
extending ``_CHECKS``.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

import pytest

from tests.e2e.baseline.settings import BaselineSettings

MARKER = "requires_deps"


class Dep(Enum):
    """An optional capability a test can require (a model-provider key)."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


# Dep -> (is-available check, skip reason when absent).
_CHECKS: dict[Dep, tuple[Callable[[BaselineSettings], bool], str]] = {
    Dep.OPENAI: (
        lambda s: bool(s.llm_credentials.openai_api_key),
        "OPENAI_API_KEY not set",
    ),
    Dep.ANTHROPIC: (
        lambda s: bool(s.llm_credentials.anthropic_api_key),
        "ANTHROPIC_API_KEY not set",
    ),
}


def require_dep(dep: Dep, settings: BaselineSettings) -> None:
    """Skip the current test if ``dep``'s capability is unavailable.

    Shared by the gate hook and by fixtures that self-gate on a capability.
    """
    check, reason = _CHECKS[dep]
    if not check(settings):
        pytest.skip(reason)


def requires(*deps: Dep) -> pytest.MarkDecorator:
    """Mark a test with the optional capabilities it needs."""
    for dep in deps:
        if not isinstance(dep, Dep):  # guard against stray strings/typos
            raise TypeError(f"requires() takes Dep members, got {dep!r}")
    return getattr(pytest.mark, MARKER)(tuple(deps))
