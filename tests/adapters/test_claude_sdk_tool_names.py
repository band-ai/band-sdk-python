"""claude_sdk surfaces bare tool names, not its MCP transport prefix.

claude_sdk exposes band + custom tools via an in-process MCP server, so the Claude
Agent SDK namespaces them ``mcp__band__<tool>``. The platform ``tool_call`` event and
the approval UX are cross-adapter, semantic records where every other adapter uses the
bare name, so the adapter strips its own server's prefix at those boundaries.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.converters.claude_sdk import ClaudeSDKSessionState
from band.core.types import AdapterFeatures, Emit, PlatformMessage


def test_semantic_tool_name_strips_only_our_server_prefix() -> None:
    strip = ClaudeSDKAdapter._semantic_tool_name
    assert strip("mcp__band__lookup") == "lookup"
    assert strip("mcp__band__band_list_memories") == "band_list_memories"
    # A bare name (or a built-in like ToolSearch) is unchanged...
    assert strip("ToolSearch") == "ToolSearch"
    # ...and an external MCP server's tools stay namespaced.
    assert strip("mcp__other__thing") == "mcp__other__thing"


@pytest.mark.asyncio
async def test_tool_call_event_uses_bare_name() -> None:
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    adapter = ClaudeSDKAdapter(
        features=AdapterFeatures(emit=frozenset({Emit.EXECUTION}))
    )

    message = PlatformMessage(
        id="m1",
        room_id="room-1",
        content="hi",
        sender_id="u1",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )
    tools = MagicMock()
    tools.send_event = AsyncMock(return_value={"status": "sent"})

    assistant = AssistantMessage(
        content=[
            ToolUseBlock(id="t1", name="mcp__band__lookup", input={"key": "alpha"})
        ],
        model="claude-test",
    )

    async def receive():
        yield assistant

    client = MagicMock()
    client.query = AsyncMock()
    client.receive_response = MagicMock(return_value=receive())
    manager = AsyncMock()
    manager.get_or_create_session = AsyncMock(return_value=client)

    with patch("band.adapters.claude_sdk.ClaudeSessionManager", return_value=manager):
        await adapter.on_started(agent_name="Bot", agent_description="d")
        await adapter.on_message(
            msg=message,
            tools=tools,
            history=ClaudeSDKSessionState(text=""),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

    tool_calls = [
        call
        for call in tools.send_event.call_args_list
        if call.kwargs.get("message_type") == "tool_call"
    ]
    assert tool_calls, "expected a tool_call event"
    assert json.loads(tool_calls[0].kwargs["content"])["name"] == "lookup"
