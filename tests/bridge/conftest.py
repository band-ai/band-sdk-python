"""Conftest for bridge tests — adds thenvoi-bridge to sys.path and shared fixtures."""

from __future__ import annotations

import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

_bridge_dir = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "thenvoi-bridge")
)
if _bridge_dir not in sys.path:
    sys.path.insert(0, _bridge_dir)

from bridge_core.bridge import BandBridge  # noqa: E402
from bridge_core.config import (  # noqa: E402
    AgentConfig,
    AgentCoreTarget,
    BridgeConfig,
    HTTPTarget,
)
from bridge_core.forwarder import Forwarder  # noqa: E402


class FakeForwarder:
    """Records all forward() calls; for use in tests."""

    def __init__(self) -> None:
        self.forwarded: list[dict[str, Any]] = []
        self.closed = False
        self.forward_side_effect: Exception | None = None

    async def forward(self, payload: dict[str, Any]) -> None:
        if self.forward_side_effect is not None:
            raise self.forward_side_effect
        self.forwarded.append(payload)

    async def close(self) -> None:
        self.closed = True


def make_http_agent(
    agent_id: str = "agent-1",
    api_key: str = "key-1",
    url: str = "https://example.com/invocations",
) -> AgentConfig:
    return AgentConfig(
        agent_id=agent_id,
        api_key=api_key,
        target=HTTPTarget(url=url),
    )


def make_agentcore_agent(
    agent_id: str = "agent-1",
    api_key: str = "key-1",
    runtime_arn: str = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/abc",
    region: str = "us-east-1",
) -> AgentConfig:
    return AgentConfig(
        agent_id=agent_id,
        api_key=api_key,
        target=AgentCoreTarget(runtime_arn=runtime_arn, region=region),
    )


def make_link_mock() -> MagicMock:
    """Build a MagicMock BandLink with all async methods stubbed.

    Used by tests that construct a BandBridge or AgentRunner without
    a real WS connection.
    """
    link = MagicMock()
    link.connect = AsyncMock()
    link.disconnect = AsyncMock()
    link.subscribe_room = AsyncMock()
    link.unsubscribe_room = AsyncMock()
    link.subscribe_agent_rooms = AsyncMock()
    link.get_next_message = AsyncMock(return_value=None)
    link.is_connected = True
    link.rest = MagicMock()
    link.rest.agent_api_chats.list_agent_chats = AsyncMock(
        return_value=MagicMock(data=None)
    )
    return link


@pytest.fixture
def http_agent() -> AgentConfig:
    return make_http_agent()


@pytest.fixture
def bridge_config(http_agent: AgentConfig) -> BridgeConfig:
    return BridgeConfig(agents=[http_agent])


@pytest.fixture
def fake_forwarder() -> FakeForwarder:
    return FakeForwarder()


@pytest.fixture
def bridge_with_fakes(
    bridge_config: BridgeConfig, fake_forwarder: FakeForwarder
) -> BandBridge:
    """BandBridge wired with a FakeForwarder and a mock link per agent."""
    forwarders: dict[str, Forwarder] = {
        a.agent_id: fake_forwarder for a in bridge_config.agents
    }
    links = {a.agent_id: make_link_mock() for a in bridge_config.agents}
    return BandBridge(
        config=bridge_config,
        forwarders=forwarders,
        links=links,
    )
