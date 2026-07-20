"""Shared sentinel value for required config fields with no real default."""

from __future__ import annotations

from os import environ


class MissingSentinel:
    """Sentinel indicating a required field was not provided."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<MISSING>"


MISSING = MissingSentinel()

IN_CI = bool(environ.get("CI") or environ.get("GITHUB_ACTIONS"))

# Strict CI mode: framework config builders raise on import failure instead of
# warning. Opt out via BAND_ALLOW_MISSING_FRAMEWORKS=1 for partial-deps CI
# environments (e.g. the dev-crewai matrix job, which only has crewai
# installed and cannot import langgraph/anthropic/parlant/pydantic-ai/etc.).
STRICT_CI = IN_CI and not environ.get("BAND_ALLOW_MISSING_FRAMEWORKS")
