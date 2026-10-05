"""Shared factories for the Letta adapter tests.

Mock builders for Letta API objects (messages, responses, agents,
conversations, MCP servers/tools, async streams) and platform messages, used
by ``test_letta_adapter.py``, ``test_letta_mcp.py``, and
``test_letta_orgscope.py``.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from letta_client.types.agents.tool_call import ToolCall
from letta_client.types.agents.tool_call_message import ToolCallMessage
from letta_client.types.agents.tool_return import ToolReturn
from letta_client.types.tool_return_message import ToolReturnMessage
from pytest_httpx import HTTPXMock
from typing_extensions import Unpack

from band.adapters.letta import LettaAdapter, LettaAdapterConfig, RoomContext
from band.core.types import FeatureKwargs, PlatformMessage
from band.integrations.letta.prompts import render_tool_enforcement
from band.integrations.mcp.engine import build_resolved_band_mcp_tool_registrations
from band.runtime.tools import CHAT_ID_FIELD_NAME


def make_platform_message(
    room_id: str = "room-1", content: str = "hello"
) -> PlatformMessage:
    return PlatformMessage(
        id=str(uuid4()),
        room_id=room_id,
        content=content,
        sender_id="user-1",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


def make_letta_message(msg_type: str, **kwargs: Any) -> MagicMock:
    """Create a fake Letta response message."""
    msg = MagicMock()
    msg.message_type = msg_type
    for key, value in kwargs.items():
        setattr(msg, key, value)
    return msg


def make_assistant_message(content: str = "Hello!") -> MagicMock:
    return make_letta_message("assistant_message", content=content)


def make_tool_call_message(
    tool_name: str = "band_send_message",
    arguments: str = '{"content": "Hi", "mentions": ["@alice"]}',
) -> ToolCallMessage:
    return make_grouped_call_message(
        [ToolCall(name=tool_name, arguments=arguments, tool_call_id=call_id(tool_name))]
    )


def make_tool_return_message(
    tool_name: str = "band_send_message",
    tool_return: str = '{"status": "ok"}',
    status: Literal["success", "error"] = "success",
) -> ToolReturnMessage:
    return make_grouped_return_message(
        [
            ToolReturn(
                type="tool",
                tool_return=tool_return,
                status=status,
                tool_call_id=call_id(tool_name),
            )
        ],
        name=tool_name,
    )


def call_id(tool_name: str) -> str:
    return f"call-{tool_name}"


def make_grouped_call_message(calls: list[ToolCall]) -> ToolCallMessage:
    """Letta's shape: ``tool_call`` repeats only the first of ``tool_calls``."""
    return ToolCallMessage(
        id=f"message-{calls[0].tool_call_id}",
        date=datetime.now(UTC),
        message_type="tool_call_message",
        tool_call=calls[0],
        tool_calls=calls,
    )


def make_grouped_return_message(
    returns: list[ToolReturn], *, name: str
) -> ToolReturnMessage:
    """Letta's shape: ``name``/``status``/``tool_call_id`` describe only the
    first of ``tool_returns``."""
    first = returns[0]
    return ToolReturnMessage(
        id=f"message-return-{first.tool_call_id}",
        date=datetime.now(UTC),
        message_type="tool_return_message",
        name=name,
        status=first.status,
        tool_call_id=first.tool_call_id,
        tool_return=str(first.tool_return),
        tool_returns=returns,
    )


def make_parallel_tool_messages(
    results: list[tuple[str, Literal["success", "error"]]],
    *,
    reverse_returns: bool,
) -> tuple[ToolCallMessage, ToolReturnMessage]:
    calls = [
        ToolCall(name=name, arguments="{}", tool_call_id=f"call-{index}")
        for index, (name, _status) in enumerate(results)
    ]
    returns = [
        ToolReturn(
            type="tool",
            tool_return="result",
            status=status,
            tool_call_id=call.tool_call_id,
        )
        for call, (_name, status) in zip(calls, results, strict=True)
    ]
    if reverse_returns:
        returns.reverse()
    return (
        make_grouped_call_message(calls),
        make_grouped_return_message(returns, name=calls[0].name),
    )


def make_letta_response(*messages: Any) -> MagicMock:
    """Create a fake Letta API response."""
    resp = MagicMock()
    resp.messages = list(messages)
    return resp


def ready_letta_adapter(
    config: LettaAdapterConfig | None = None,
    *,
    room_id: str = "room-1",
    **features: Unpack[FeatureKwargs],
) -> tuple[LettaAdapter, AsyncMock]:
    """An adapter past startup: client set, MCP path registered, and a Letta
    agent already bound to ``room_id``. Returns it with its mock client."""
    adapter = LettaAdapter(config, **features)
    client = AsyncMock()
    adapter._client = client
    adapter._system_prompt = "Test"
    adapter._mcp.server_id = "mcp-server-1"
    adapter._rooms[room_id] = RoomContext(agent_id="agent-1")
    return adapter, client


def scripted_letta_turn(
    adapter: LettaAdapter,
    *,
    room_id: str,
    tool_calls: Sequence[tuple[str, dict[str, Any]]] = (),
    final_text: str = "",
) -> Callable[..., Awaitable[MagicMock]]:
    """A self-hosted Letta agent's turn, as a ``messages.create`` side effect.

    Each tool call runs through the adapter's real Band MCP registrations (the
    path Letta's MCP client reaches), then the turn's messages come back as
    Letta's response.
    """
    registrations = {
        registration.name: registration
        for registration in build_resolved_band_mcp_tool_registrations(
            get_tools=adapter._get_room_tools,
            capabilities=adapter.features.capabilities,
        )
    }

    async def create(**_kwargs: Any) -> MagicMock:
        messages: list[Any] = []
        for name, arguments in tool_calls:
            messages.append(make_tool_call_message(name, json.dumps(arguments)))
            result = await registrations[name].execute(
                {**arguments, CHAT_ID_FIELD_NAME: room_id}
            )
            messages.append(make_tool_return_message(name, result))
        if final_text:
            messages.append(make_assistant_message(final_text))
        return make_letta_response(*messages)

    return create


def make_mock_mcp_server(server_id: str = "mcp-server-1") -> MagicMock:
    """Create a mock MCP server response."""
    server = MagicMock()
    server.id = server_id
    return server


def make_mock_mcp_tool(tool_id: str, tool_name: str) -> MagicMock:
    """Create a mock MCP tool response."""
    tool = MagicMock()
    tool.id = tool_id
    tool.name = tool_name
    return tool


def make_mock_agent(agent_id: str = "agent-123") -> MagicMock:
    """Create a mock agent response."""
    agent = MagicMock()
    agent.id = agent_id
    return agent


def make_mock_conversation(
    conversation_id: str = "conv-123", *, agent_id: str | None = None
) -> MagicMock:
    """Create a mock conversation response."""
    conv = MagicMock()
    conv.id = conversation_id
    if agent_id is not None:
        conv.agent_id = agent_id
    return conv


def make_mock_tool_page(*tools: MagicMock) -> MagicMock:
    """Create a mock paginated tool list response."""
    page = MagicMock()
    page.items = list(tools)
    return page


def make_fake_mcp_backend(port: int = 55321) -> MagicMock:
    """Create a fake self-hosted Band MCP backend (create_band_mcp_backend result)."""
    backend = MagicMock()
    backend.local_server = MagicMock()
    backend.local_server.port = port
    backend.allowed_tools = ["mcp__band__band_send_message"]
    backend.stop = AsyncMock()
    return backend


def default_enforcement(room_id: str | None = None) -> str:
    """The enforcement preamble with the default (band_*) tool names."""
    return render_tool_enforcement(
        "band_send_message", "band_send_event", room_id=room_id
    )


def mock_org_user_provisioned(
    httpx_mock: HTTPXMock,
    *,
    base_url: str,
    org_id: str,
    user_id: str,
    name: str,
) -> None:
    """Wire httpx_mock for a fresh org+user provisioned under ``name``.

    Matches a no-existing-org/user startup, including the post-create read-back
    that makes the created identities safe to use. Tests covering pagination or
    duplicate edge cases stay inline next to what they cover.
    """
    httpx_mock.add_response(method="GET", url=f"{base_url}/v1/admin/orgs/", json=[])
    httpx_mock.add_response(
        method="POST",
        url=f"{base_url}/v1/admin/orgs/",
        json={"id": org_id, "name": name},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{base_url}/v1/admin/orgs/",
        json=[{"id": org_id, "name": name}],
        is_optional=True,
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{base_url}/v1/admin/orgs/?after={org_id}",
        json=[],
        is_optional=True,
    )
    httpx_mock.add_response(method="GET", url=f"{base_url}/v1/admin/users/", json=[])
    httpx_mock.add_response(
        method="POST",
        url=f"{base_url}/v1/admin/users/",
        json={"id": user_id, "name": name, "organization_id": org_id},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{base_url}/v1/admin/users/",
        json=[{"id": user_id, "name": name, "organization_id": org_id}],
        is_optional=True,
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{base_url}/v1/admin/users/?after={user_id}",
        json=[],
        is_optional=True,
    )


def make_mock_async_stream(*messages: MagicMock) -> Any:
    """Create a mock async stream yielding Letta messages."""

    class _AsyncStream:
        def __init__(self, stream_messages: list[MagicMock]) -> None:
            self._messages = stream_messages

        async def __aiter__(self) -> Any:
            for stream_message in self._messages:
                yield stream_message

    return _AsyncStream(list(messages))
