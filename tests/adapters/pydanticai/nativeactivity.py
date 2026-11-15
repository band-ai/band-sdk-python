"""Explicit native body outcomes for real framework execution tests."""

from __future__ import annotations

NATIVE_FAILURE = "native failure"


def succeed() -> str:
    return "opaque result"


def fail() -> str:
    raise ValueError(NATIVE_FAILURE)
