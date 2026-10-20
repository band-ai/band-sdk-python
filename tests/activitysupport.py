"""Isolate the process-wide activity-detail negotiation per test."""

from __future__ import annotations

import pytest

import band.platform.message_lifecycle as lifecycle
from band.platform.activity import (
    API_VERSION_HEADER,
    MINIMUM_ACTIVITY_VERSION,
    ActivitySupport,
)

SUPPORTED_VERSION = ".".join(map(str, MINIMUM_ACTIVITY_VERSION))


def install_activity_support(
    monkeypatch: pytest.MonkeyPatch, *, learned: bool
) -> ActivitySupport:
    """Give the lifecycle a fresh negotiation, optionally already supported."""
    support = ActivitySupport()
    if learned:
        support.observe({API_VERSION_HEADER: SUPPORTED_VERSION})
    monkeypatch.setattr(lifecycle, "activity_support", support)
    return support
