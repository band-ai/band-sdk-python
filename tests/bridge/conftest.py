"""Conftest for bridge tests — adds thenvoi-bridge to sys.path and shared fixtures."""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add thenvoi-bridge directory to sys.path so we can import bridge_core.* and handlers.*
# Use os.path.abspath for the check and insertion to avoid duplicates from
# different relative representations of the same path.
_bridge_dir = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "thenvoi-bridge")
)
if _bridge_dir not in sys.path:
    sys.path.insert(0, _bridge_dir)

from bridge_core.bridge import BridgeConfig, BandBridge  # noqa: E402


def make_tools(
    participants: list[dict] | None = None,
    send_event_side_effect: Exception | None = None,
) -> MagicMock:
    """Create a mock AgentTools with common defaults.

    Shared across all bridge handler tests.
    """
    tools = MagicMock()
    tools.send_message = AsyncMock()
    tools.send_event = AsyncMock(side_effect=send_event_side_effect)
    tools.participants = participants or []
    return tools


@pytest.fixture
def bridge_config() -> BridgeConfig:
    """Standard bridge config for tests."""
    return BridgeConfig(
        agent_id="agent-1",
        api_key="key-1",
        agent_mapping="alice:handler_a",
    )


@pytest.fixture
def bridge_with_mock_link(bridge_config: BridgeConfig) -> BandBridge:
    """Bridge instance with a mocked link and handler."""
    handler = AsyncMock()
    b = BandBridge(config=bridge_config, handlers={"handler_a": handler})
    mock_link = MagicMock()
    mock_link.subscribe_room = AsyncMock()
    mock_link.unsubscribe_room = AsyncMock()
    mock_link.rest = MagicMock()
    b._link = mock_link
    b._router._link = mock_link
    return b


@pytest.fixture
def bridge_with_full_mock(bridge_config: BridgeConfig) -> BandBridge:
    """Bridge with all link methods mocked (superset of bridge_with_mock_link).

    Covers connect, disconnect, subscribe/unsubscribe, lifecycle marks,
    and REST endpoints needed by dedup, participant cache, and
    connect-and-consume tests.
    """
    handler = AsyncMock()
    b = BandBridge(config=bridge_config, handlers={"handler_a": handler})
    mock_link = MagicMock()
    mock_link.connect = AsyncMock()
    mock_link.disconnect = AsyncMock()
    mock_link.subscribe_room = AsyncMock()
    mock_link.unsubscribe_room = AsyncMock()
    mock_link.subscribe_agent_rooms = AsyncMock()
    mock_link.mark_processing = AsyncMock()
    mock_link.mark_processed = AsyncMock()
    mock_link.rest = MagicMock()
    mock_link.rest.agent_api_chats.list_agent_chats = AsyncMock(
        return_value=MagicMock(data=None)
    )
    b._link = mock_link
    b._router._link = mock_link
    return b
