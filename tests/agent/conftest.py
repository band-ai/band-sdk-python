"""Shared fixtures for Agent compositor tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.types import SessionConfig


@pytest.fixture
def mock_adapter():
    """Create mock adapter."""
    adapter = AsyncMock()
    adapter.on_started = AsyncMock()
    adapter.on_cleanup = AsyncMock()
    adapter.on_event = AsyncMock()
    adapter.try_handle_control = None
    return adapter


@pytest.fixture
def mock_runtime():
    """Create mock PlatformRuntime."""
    runtime = MagicMock()
    runtime.agent_name = "TestBot"
    runtime.agent_description = "A test bot"
    runtime.agent_id = "agent-123"
    runtime.feature_flags = None
    runtime.initialize = AsyncMock()
    runtime.start = AsyncMock()
    runtime.stop = AsyncMock()
    runtime.run_forever = AsyncMock()
    runtime.cleanup_pending = False
    runtime.session_config = SessionConfig()
    return runtime


@pytest.fixture
def mock_preprocessor():
    """Create mock Preprocessor."""
    preprocessor = AsyncMock()
    preprocessor.process = AsyncMock(return_value=None)
    return preprocessor
