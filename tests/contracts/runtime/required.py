"""Assert presence of optional records before inspecting their required contents."""

from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


def require_value(value: T | None, message: str) -> T:
    assert value is not None, message
    return value
