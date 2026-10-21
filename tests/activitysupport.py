"""Activity report bodies and per-test isolation of the detail negotiation."""

from __future__ import annotations

from typing import Any

import pytest

import band.platform.message_lifecycle as lifecycle
from band.platform.activity import (
    API_VERSION_HEADER,
    MINIMUM_ACTIVITY_VERSION,
    ActivitySupport,
)
from band.runtime.activity import MEMORY, PERMISSION, REASONING, TOOL_CALL

SUPPORTED_VERSION = ".".join(map(str, MINIMUM_ACTIVITY_VERSION))

BOOLEAN_BODY = {"working": True}
IDLE_BODY = {"working": False}


def detail_body(kind: str, name: str | None = None) -> dict[str, Any]:
    activity = {"kind": kind} if name is None else {"kind": kind, "name": name}
    return {**BOOLEAN_BODY, "activity": activity}


def tool_body(name: str) -> dict[str, Any]:
    return detail_body(TOOL_CALL, name)


REASONING_BODY = detail_body(REASONING.kind)
PERMISSION_BODY = detail_body(PERMISSION.kind)
MEMORY_BODY = detail_body(MEMORY.kind)
DETAIL_BODY = tool_body("Bash")
DETAIL = DETAIL_BODY["activity"]


def install_activity_support(
    monkeypatch: pytest.MonkeyPatch, *, learned: bool
) -> ActivitySupport:
    """Give the lifecycle a fresh negotiation, optionally already supported."""
    support = ActivitySupport()
    if learned:
        support.observe({API_VERSION_HEADER: SUPPORTED_VERSION})
    monkeypatch.setattr(lifecycle, "activity_support", support)
    return support
