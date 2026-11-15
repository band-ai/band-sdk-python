"""Shared helpers for the Letta adapter tests."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from band.core.types import ToolEventKey
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools, events_of_type


class FakeMcpToolsList:
    """Returns a fixed discovery list for ``mcp_servers.tools.list``."""

    def __init__(self, tools: list[Any]) -> None:
        self._tools = tools

    async def list(self, *, mcp_server_id: str) -> list[Any]:
        assert mcp_server_id
        return list(self._tools)


class FakeMcpServers:
    """Minimal Letta ``mcp_servers`` namespace for external register tests."""

    def __init__(self, tools: list[Any]) -> None:
        self.tools = FakeMcpToolsList(tools)

    async def list(self) -> list[Any]:
        return []

    async def create(self, config: Any = None, **kwargs: Any) -> Any:
        name = (
            getattr(config, "server_name", None) or kwargs.get("server_name") or "band"
        )
        return SimpleNamespace(id="srv-ext-1", server_name=name, name=name)


class FakeLettaMcpClient:
    """Letta client stub that only serves MCP discovery/create."""

    def __init__(self, tools: list[Any]) -> None:
        self.mcp_servers = FakeMcpServers(tools)


# Band tool calls that settle the turn's reply, so no assistant text is relayed.
REPLY_SETTLING_CALLS = [
    pytest.param(
        BandTool.SEND_MESSAGE, {"content": "Hi", "mentions": ["@alice"]}, id="message"
    ),
    pytest.param(
        BandTool.SEND_ROOM_FILE,
        {"content": "notes", "filename": "notes.txt", "mentions": ["@alice"]},
        id="room-file",
    ),
    pytest.param(BandTool.NO_REPLY, {"reason": "FYI only"}, id="no-reply"),
]


def sent_contents(tools: FakeAgentTools) -> list[str]:
    return [message["content"] for message in tools.messages_sent]


def reported_tool_names(tools: FakeAgentTools, event_type: str) -> list[str]:
    return [
        json.loads(event["content"])[ToolEventKey.NAME]
        for event in events_of_type(tools, event_type)
    ]
