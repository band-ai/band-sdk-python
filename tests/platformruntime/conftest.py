"""Shared PlatformRuntime fixtures."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_link():
    """Create mock BandLink."""
    link = MagicMock()
    link.rest = MagicMock()
    link.rest.agent_api_identity = MagicMock()
    link.disconnect = AsyncMock()
    link.run_forever = AsyncMock()

    # Mock agent metadata response
    mock_agent = MagicMock()
    mock_agent.name = "TestBot"
    mock_agent.description = "A test bot"

    mock_response = MagicMock()
    mock_response.data = mock_agent
    link.rest.agent_api_identity.get_agent_me = AsyncMock(return_value=mock_response)

    return link


@pytest.fixture
def mock_runtime():
    """Create mock AgentRuntime."""
    runtime = MagicMock()
    runtime.start = AsyncMock()
    runtime.stop = AsyncMock()
    return runtime


@pytest.fixture
def platform_mocks(mock_link, mock_runtime):
    """Run a test with BandLink and AgentRuntime replaced by the mocks."""
    with (
        patch("band.runtime.platform_runtime.BandLink", return_value=mock_link),
        patch("band.runtime.platform_runtime.AgentRuntime", return_value=mock_runtime),
    ):
        yield
