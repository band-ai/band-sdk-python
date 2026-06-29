"""Declarative test-dependency requirements (pytest glue).

``@requires(Dep.OPENAI, ...)`` declares the requirements a test (or a matrix cell)
needs; it attaches a marker resolved by a hook in conftest.py. The always-on gate
(E2E enabled, BAND_API_KEY_USER present) is applied to every baseline test by that
hook -- it is not a dependency you pass.

Validation policy: a missing requirement **fails** the test, it never skips.
Skipping a test because a key/CLI/server is absent hides misconfiguration as
false-green. The only thing that skips is the ``E2E_TESTS_ENABLED`` master switch
(the deliberate on/off for the whole live suite).

The ``Dep`` enum and its facts live in the pytest-free ``toolkit.requirements``
module (so the adapter registry can reference ``Dep`` without importing pytest);
add a requirement by adding a ``Dep`` member and a ``_DEPS`` entry there.
"""

from __future__ import annotations

import pytest

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.requirements import Dep, requirement_reason

__all__ = ["MARKER", "Dep", "require_dep", "requires"]

MARKER = "requires_deps"


def require_dep(dep: Dep, settings: BaselineSettings) -> None:
    """Fail the current test if ``dep``'s requirement is unavailable.

    Missing config that a test needs is a hard failure, never a skip (a skip
    would hide misconfiguration). Shared by the gate hook and by fixtures that
    self-gate on a requirement.
    """
    reason = requirement_reason(dep, settings)
    if reason is not None:
        pytest.fail(reason)


def requires(*deps: Dep) -> pytest.MarkDecorator:
    """Mark a test with the requirements it needs (each fails if absent)."""
    for dep in deps:
        if not isinstance(dep, Dep):  # guard against stray strings/typos
            raise TypeError(f"requires() takes Dep members, got {dep!r}")
    return getattr(pytest.mark, MARKER)(tuple(deps))
