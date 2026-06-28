"""Declarative test-dependency requirements.

``@requires(Dep.OPENAI, ...)`` declares the requirements a test needs (provider
keys, a second user, ...); it attaches a marker resolved by a hook in
conftest.py. The always-on gate (E2E enabled, BAND_API_KEY_USER present) is
applied to every baseline test by that hook -- it is not a dependency you pass.

Validation policy: a missing requirement **fails** the test, it never skips.
Skipping a test because a key is absent hides misconfiguration as false-green.
The only thing that skips is the ``E2E_TESTS_ENABLED`` master switch (the
deliberate on/off for the whole live suite). Add a requirement by extending
``_CHECKS``.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

import pytest

from tests.e2e.baseline.settings import BaselineSettings

MARKER = "requires_deps"


class Dep(Enum):
    """A requirement a test can declare (a model-provider key)."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


# Dep -> (is-available check, failure reason when absent).
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
    """Fail the current test if ``dep``'s requirement is unavailable.

    Missing config that a test needs is a hard failure, never a skip (a skip
    would hide misconfiguration). Shared by the gate hook and by fixtures that
    self-gate on a requirement.
    """
    check, reason = _CHECKS[dep]
    if not check(settings):
        pytest.fail(reason)


def requires(*deps: Dep) -> pytest.MarkDecorator:
    """Mark a test with the requirements it needs (each fails if absent)."""
    for dep in deps:
        if not isinstance(dep, Dep):  # guard against stray strings/typos
            raise TypeError(f"requires() takes Dep members, got {dep!r}")
    return getattr(pytest.mark, MARKER)(tuple(deps))
