"""Fixtures for the room readiness tests."""

from __future__ import annotations

import pytest

from tests.websocket.client.readiness.kit import Timeline


@pytest.fixture
def timeline() -> Timeline:
    return Timeline()
