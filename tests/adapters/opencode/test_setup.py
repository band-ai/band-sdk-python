"""Tests for OpencodeAdapter."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from band import BandConfigError
from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.core.types import Capability
from band.integrations.opencode.types import OpencodeSessionState
from band.runtime.tools import CONTACT_TOOL_NAMES, MEMORY_TOOL_NAMES
from band.testing import FakeAgentTools


from tests.adapters.opencode.helpers import (
    FakeMCPBackend,
    FakeOpencodeClient,
    make_fake_mcp_backend_factory,
    run_single_turn,
    event_message_updated,
    event_session_idle,
    event_text_part,
    make_platform_message,
    tools_protocol,
)


async def test_startup_fails_loudly_when_server_unreachable() -> None:
    """The default (real-server) path must fail at startup naming the fix."""
    import httpx

    adapter = OpencodeAdapter()
    with patch(
        "band.integrations.opencode.client.HttpOpencodeClient.health",
        side_effect=httpx.ConnectError("All connection attempts failed"),
    ):
        with pytest.raises(BandConfigError, match="opencode serve"):
            await adapter.on_started("Tom", "A cat")


async def test_mcp_server_name_is_stable_per_agent_and_distinct_per_agent() -> None:
    def factory(_config: OpencodeAdapterConfig) -> FakeOpencodeClient:
        return FakeOpencodeClient()

    first = OpencodeAdapter(client_factory=factory)
    restarted = OpencodeAdapter(client_factory=factory)
    other = OpencodeAdapter(client_factory=factory)

    await first.on_started("Tom", "A cat")
    await restarted.on_started("Tom", "A cat")
    await other.on_started("Jerry", "A mouse")

    assert first._mcp_server_name == restarted._mcp_server_name
    assert first._mcp_server_name != other._mcp_server_name


async def test_mcp_registration_uses_band_agent_id_before_startup() -> None:
    class IdentityTools(FakeAgentTools):
        @property
        def agent_id(self) -> str:
            return "agent-123"

    fake_backend = FakeMCPBackend()
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[[event_session_idle("sess-1")]]
    )
    adapter = OpencodeAdapter(client_factory=lambda _config: fake_client)

    with patch(
        "band.adapters.opencode.adapter.create_band_mcp_backend",
        make_fake_mcp_backend_factory(fake_backend),
    ):
        await adapter.on_started("Renameable Agent", "")
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(IdentityTools()),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

    expected = adapter._agent_mcp_server_name("agent-123")
    assert fake_client.registered_mcp_servers == [
        {"name": expected, "url": "http://127.0.0.1:50000/sse"}
    ]

    await adapter.on_cleanup("room-1")


async def test_registers_shared_mcp_backend_with_additional_tools(
    make_adapter, tools
) -> None:
    class EchoInput(BaseModel):
        """Echo text."""

        text: str

    def echo_tool(input_data: EchoInput) -> str:
        return input_data.text

    fake_backend = FakeMCPBackend(sse_url="http://127.0.0.1:50000/sse")
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-1"),
                event_text_part("sess-1", "msg-1", "hello"),
                event_session_idle("sess-1"),
            ]
        ]
    )
    adapter = OpencodeAdapter(
        additional_tools=[(EchoInput, echo_tool)],
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()
    backend_factory = make_fake_mcp_backend_factory(fake_backend)

    with patch(
        "band.adapters.opencode.adapter.create_band_mcp_backend",
        backend_factory,
    ):
        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

    assert fake_client.registered_mcp_servers == [
        {"name": adapter._mcp_server_name, "url": "http://127.0.0.1:50000/sse"},
    ]
    assert backend_factory.await_args.kwargs["additional_tools"] == [
        (EchoInput, echo_tool)
    ]

    await adapter.on_cleanup("room-1")


async def test_prompt_scopes_tools_to_this_agents_mcp_registration(
    make_adapter, tools
) -> None:
    """Concurrent agents share one `opencode serve`, which keys MCP
    registrations globally. Every turn therefore denies the shared `band_*`
    namespace and re-allows only this agent's own registration -- in that order,
    because OpenCode applies the LAST matching rule."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[[event_session_idle("sess-1")]]
    )
    adapter = make_adapter(fake_client)

    await run_single_turn(adapter, tools)

    registered = fake_client.registered_mcp_servers[0]["name"]
    assert list(fake_client.prompt_calls[0]["tools"].items()) == [
        ("band_*", False),
        (f"{registered}_*", True),
    ]


async def test_registers_shared_mcp_backend_on_startup() -> None:
    fake_backend = FakeMCPBackend()
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-1"),
                event_text_part("sess-1", "msg-1", "hello"),
                event_session_idle("sess-1"),
            ]
        ]
    )
    adapter = OpencodeAdapter(
        client_factory=lambda _config: fake_client,
    )
    tools = FakeAgentTools()

    with patch(
        "band.adapters.opencode.adapter.create_band_mcp_backend",
        make_fake_mcp_backend_factory(fake_backend),
    ):
        await adapter.on_started("OpenCode Agent", "A coding agent")
        await adapter.on_message(
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

    assert fake_client.registered_mcp_servers == [
        {"name": adapter._mcp_server_name, "url": "http://127.0.0.1:50000/sse"}
    ]
    assert fake_client.prompt_calls[0]["tools"] == {
        "band_*": False,
        f"{adapter._mcp_server_name}_*": True,
    }
    assert list(fake_client.prompt_calls[0]["tools"]) == [
        "band_*",
        f"{adapter._mcp_server_name}_*",
    ]

    await adapter.on_cleanup("room-1")
    assert fake_client.disconnected_mcp_servers == [adapter._mcp_server_name]
    assert fake_backend.stop_calls == 1


async def test_bootstrap_creates_session_relays_text_and_persists_task(
    make_adapter, tools
) -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-1"),
                event_text_part("sess-1", "msg-1", "OpenCode says hi"),
                event_session_idle("sess-1"),
            ]
        ]
    )
    adapter = make_adapter(fake_client)

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(),
        tools_protocol(tools),
        OpencodeSessionState(),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    assert fake_client.created_sessions[0]["id"] == "sess-1"
    assert tools.messages_sent[0]["content"] == "OpenCode says hi"
    assert tools.messages_sent[0]["mentions"] == [{"id": "user-1"}]
    task_events = [e for e in tools.events_sent if e["message_type"] == "task"]
    assert task_events
    assert task_events[0]["metadata"]["opencode_session_id"] == "sess-1"
    assert (
        task_events[0]["metadata"]["opencode_mcp_server_name"]
        == adapter._mcp_server_name
    )


async def test_reuses_persisted_session(make_adapter, tools) -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-existing", "msg-2"),
                event_text_part("sess-existing", "msg-2", "Reused session"),
                event_session_idle("sess-existing"),
            ]
        ]
    )
    adapter = make_adapter(fake_client)

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(),
        tools_protocol(tools),
        OpencodeSessionState(
            session_id="sess-existing",
            mcp_server_name=adapter._mcp_server_name,
            room_id="room-1",
        ),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    assert fake_client.created_sessions == []
    assert fake_client.prompt_calls[0]["session_id"] == "sess-existing"
    assert tools.messages_sent[0]["content"] == "Reused session"


async def test_replaces_session_from_another_mcp_registration(
    make_adapter, tools
) -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[[event_session_idle("sess-1")]]
    )
    adapter = make_adapter(fake_client)

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(),
        tools_protocol(tools),
        OpencodeSessionState(
            session_id="sess-stale",
            mcp_server_name="band_old",
            room_id="room-1",
        ),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )

    assert fake_client.created_sessions[0]["id"] == "sess-1"
    assert fake_client.prompt_calls[0]["session_id"] == "sess-1"


async def test_missing_session_replays_history_into_new_prompt(
    make_adapter, tools
) -> None:
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[
            [
                event_message_updated("sess-1", "msg-6"),
                event_text_part("sess-1", "msg-6", "Session recreated"),
                event_session_idle("sess-1"),
            ]
        ],
        get_session_missing={"sess-missing"},
    )
    adapter = make_adapter(fake_client)

    await adapter.on_started("OpenCode Agent", "A coding agent")
    await adapter.on_message(
        make_platform_message(content="Continue from before"),
        tools_protocol(tools),
        OpencodeSessionState(
            session_id="sess-missing",
            room_id="room-1",
            replay_messages=[
                "[Alice]: Earlier question",
                "[OpenCode Agent]: Earlier answer",
            ],
        ),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )

    prompt_text = fake_client.prompt_calls[0]["parts"][0]["text"]
    assert fake_client.created_sessions[0]["id"] == "sess-1"
    assert "Recovered room history" in prompt_text
    assert "[Alice]: Earlier question" in prompt_text
    assert "[OpenCode Agent]: Earlier answer" in prompt_text


async def test_capability_gating_controls_registered_tool_set(
    make_adapter, tools
) -> None:
    """Capability.MEMORY / Capability.CONTACTS gate which platform tools
    the adapter registers with OpenCode's shared MCP backend, since a
    bare adapter (no capabilities) must not expose them."""
    captured_tool_names: list[frozenset[str]] = []

    async def capturing_factory(**kwargs: Any) -> FakeMCPBackend:
        captured_tool_names.append(
            frozenset(definition.name for definition in kwargs["tool_definitions"])
        )
        return FakeMCPBackend()

    with patch(
        "band.adapters.opencode.adapter.create_band_mcp_backend",
        AsyncMock(side_effect=capturing_factory),
    ):
        bare_adapter = OpencodeAdapter(
            client_factory=lambda _config: FakeOpencodeClient(
                prompt_event_sequences=[[event_session_idle("sess-1")]]
            ),
        )
        await bare_adapter.on_started("OpenCode Agent", "A coding agent")
        await bare_adapter.on_message(
            make_platform_message(),
            tools_protocol(FakeAgentTools()),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await bare_adapter.on_cleanup("room-1")

        full_adapter = OpencodeAdapter(
            client_factory=lambda _config: FakeOpencodeClient(
                prompt_event_sequences=[[event_session_idle("sess-1")]]
            ),
            capabilities={Capability.MEMORY, Capability.CONTACTS},
        )
        await full_adapter.on_started("OpenCode Agent", "A coding agent")
        await full_adapter.on_message(
            make_platform_message(),
            tools_protocol(FakeAgentTools()),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        await full_adapter.on_cleanup("room-1")

    bare_tool_names, full_tool_names = captured_tool_names
    assert bare_tool_names.isdisjoint(MEMORY_TOOL_NAMES)
    assert bare_tool_names.isdisjoint(CONTACT_TOOL_NAMES)
    assert MEMORY_TOOL_NAMES <= full_tool_names
    assert CONTACT_TOOL_NAMES <= full_tool_names


def test_own_band_tools_recognized_before_mcp_registration() -> None:
    """The auto-approve check for the adapter's own band tools must not wait on
    MCP registration: a second room's first turn can hit a band-tool permission
    ask while the first room is still registering. The names are derived at
    construction, so recognition holds immediately -- before on_started,
    on_message, or any register_mcp_server call."""
    adapter = OpencodeAdapter(
        client_factory=lambda _config: FakeOpencodeClient(),
        capabilities={Capability.MEMORY, Capability.CONTACTS},
    )

    # Nothing has been registered with OpenCode yet.
    assert adapter._is_own_band_tool("band_send_message")
    # OpenCode reports MCP tools with the server-name prefix; that is recognized.
    assert adapter._is_own_band_tool(f"{adapter._mcp_server_name}_band_send_message")
    # Capability-gated tools are present because the capability was enabled.
    assert MEMORY_TOOL_NAMES <= adapter._own_tool_names
    assert CONTACT_TOOL_NAMES <= adapter._own_tool_names


async def test_turn_system_prompt_carries_room_context(make_adapter, tools) -> None:
    """The per-turn system prompt must name the current room_id (band MCP
    tool schemas require a room_id argument, so an untold model cannot
    call any platform tool) and the requester."""
    fake_client = FakeOpencodeClient(
        prompt_event_sequences=[[event_session_idle("sess-1")]]
    )
    adapter = make_adapter(fake_client)

    await run_single_turn(adapter, tools)

    system = fake_client.prompt_calls[0]["system"]
    assert "Current room_id: room-1" in system
    assert "Current requester name: Alice" in system
    assert "Current requester id: user-1" in system
