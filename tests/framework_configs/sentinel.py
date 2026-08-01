"""Shared sentinel value for required config fields with no real default."""

from __future__ import annotations

from band.core.bases import BandSettings


class MissingSentinel:
    """Sentinel indicating a required field was not provided."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<MISSING>"


MISSING = MissingSentinel()


class StrictnessSettings(BandSettings):
    """Env switches deciding whether a missing framework is fatal.

    Parsed as booleans, not merely tested for presence: CI sets the opt-out
    explicitly per matrix cell, so a cell that wants strictness sends
    ``BAND_ALLOW_MISSING_FRAMEWORKS=0`` — which a presence check would read as
    "opt out" and silently turn strictness off.
    """

    ci: bool = False
    github_actions: bool = False
    band_allow_missing_frameworks: bool = False


_strictness = StrictnessSettings()

IN_CI = _strictness.ci or _strictness.github_actions

# Strict CI mode: framework config builders raise on import failure instead of
# warning. Opt out via BAND_ALLOW_MISSING_FRAMEWORKS=1 for partial-deps CI
# environments (e.g. the dev-crewai matrix job, which only has crewai
# installed and cannot import langgraph/anthropic/parlant/pydantic-ai/etc.).
STRICT_CI = IN_CI and not _strictness.band_allow_missing_frameworks
