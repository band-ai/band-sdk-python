"""Fixtures for Agent contact event wiring tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.core.simple_adapter import SimpleAdapter
from band.core.types import AdapterFeatures


@pytest.fixture
def mock_adapter():
    """Create a mock adapter."""
    adapter = MagicMock(spec=SimpleAdapter)
    adapter.on_started = AsyncMock()
    adapter.on_cleanup = AsyncMock()
    adapter.on_event = AsyncMock()
    # A real SimpleAdapter sets `.features` in __init__; spec=SimpleAdapter
    # doesn't know about instance attributes, so Agent.start()'s capability
    # negotiation needs it configured explicitly.
    adapter.features = AdapterFeatures()
    return adapter
