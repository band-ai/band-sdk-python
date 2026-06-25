"""Declarative test-dependency requirements.

``@requires(...)`` replaces scattered ``@requires_e2e`` markers and inline
``pytest.skip`` checks with one declaration of what a test needs. The
``requires_deps`` marker it attaches is resolved by a hook in conftest.py.

A prerequisite *gate* is always applied (no need to pass it): E2E disabled
skips; E2E enabled but a Band key missing fails loudly (a real misconfig). The
``Dep`` enum lists only the *optional* capabilities a test may additionally
require — currently model-provider keys, which skip when absent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

import pytest

from tests.e2e.baseline.settings import BaselineSettings

MARKER = "requires_deps"


class Dep(Enum):
    """Optional capabilities a test can require (mandatory Band keys are the
    always-on gate, not members here)."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class Disposition(Enum):
    SKIP = "skip"  # absent capability: this run just can't exercise it
    FAIL = "fail"  # absent prerequisite while enabled: a misconfiguration


@dataclass(frozen=True)
class Requirement:
    check: Callable[[BaselineSettings], bool]
    reason: str
    disposition: Disposition


_REGISTRY: dict[Dep, Requirement] = {
    Dep.OPENAI: Requirement(
        check=lambda s: bool(s.llm_credentials.openai_api_key),
        reason="OPENAI_API_KEY not set",
        disposition=Disposition.SKIP,
    ),
    Dep.ANTHROPIC: Requirement(
        check=lambda s: bool(s.llm_credentials.anthropic_api_key),
        reason="ANTHROPIC_API_KEY not set",
        disposition=Disposition.SKIP,
    ),
}


def requirement_for(dep: Dep) -> Requirement:
    return _REGISTRY[dep]


def requires(*deps: Dep) -> pytest.MarkDecorator:
    """Mark a test with its optional dependencies (the gate is always applied).

    ``@requires()`` == E2E gate only; ``@requires(Dep.OPENAI, Dep.ANTHROPIC)``
    additionally requires those provider keys.
    """
    for dep in deps:
        if not isinstance(dep, Dep):  # guard against stray strings/typos
            raise TypeError(f"requires() takes Dep members, got {dep!r}")
    return getattr(pytest.mark, MARKER)(tuple(deps))
