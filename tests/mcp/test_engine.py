"""Real protocol-level tests for the MCP engine.

Real MCP round trips over the SDK's in-memory transport
(``mcp.shared.memory.create_connected_server_and_client_session``); the only
fake is the tools layer (``FakeAgentTools``/``FakeHumanTools``) -- no
patching of engine internals.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp import ClientSession
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel, Field

from band.integrations.mcp.engine import (
    CustomToolSpec,
    EmbeddedResolver,
    EngineSpec,
    SendEventWideInput,
    build_custom_tool_registration,
    build_engine,
    build_tool_registration,
    extend_with_chat_id,
    pin_existing_chat_id,
    validate_unique_tool_names,
)
from band.runtime.tools import TOOL_DEFINITIONS
from band.testing.fake_tools import FakeAgentTools
from band_mcp import shared as shared_mod
from band_mcp.config import Config
from band_mcp.server import standalone_spec
from band_mcp.shared import AGENT_TOOLS_CACHE_MAX_SIZE, StandaloneResolver
from tests.mcp.conftest import FakeHumanTools


async def _list_tool(session: ClientSession, name: str) -> Any:
    result = await session.list_tools()
    return next((tool for tool in result.tools if tool.name == name), None)


async def _call(session: ClientSession, name: str, **arguments: object) -> Any:
    """Call a tool and parse its text content -- the engine's real wire shape
    (row 15: every registration returns a JSON *string*, matching how a real
    MCP client / LiveHarness reads it, not FastMCP's structuredContent wrapper)."""
    result = await session.call_tool(name, arguments)
    assert not result.isError, result.content
    text = result.content[0].text if result.content else None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def _agent_resolver(fake: FakeAgentTools) -> EmbeddedResolver:
    """A resolver that always returns the same room-scoped fake -- mirrors
    the embedded door's uniform routing for a single-room test."""
    return EmbeddedResolver(get_tools=lambda chat_id: fake)


async def _direct_call(mcp: FastMCP, name: str, **kwargs: object) -> Any:
    """Dispatch straight through ``_tool_manager.call_tool`` -- the engine's
    own entry point, one layer below a ``ClientSession`` round trip. Matches
    ``tests/mcp/test_fake_human_tools.py``'s ``_call`` helper."""
    raw = await mcp._tool_manager.call_tool(name, kwargs)
    assert isinstance(raw, str)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


class TestBuildEngineHostForwarding:
    """``build_engine``'s ``host`` param (regression, found live via the Letta
    lane): FastMCP's own constructor auto-enables loopback-only DNS-rebinding
    protection whenever ``transport_security is None and host in
    ("127.0.0.1", "localhost", "::1")`` -- unconditionally, since a caller
    never told it otherwise, FastMCP always saw its own ``host="127.0.0.1"``
    default and took that branch even when the real caller (LocalMCPServer)
    was bound to a non-loopback host for a documented Docker-callback case,
    rejecting every real caller with a 421."""

    def test_default_host_still_gets_loopback_protection(self) -> None:
        mcp = build_engine(EngineSpec(name="test", tools=()))
        settings = mcp.settings.transport_security
        assert settings is not None
        assert settings.enable_dns_rebinding_protection is True
        assert "127.0.0.1:*" in settings.allowed_hosts

    def test_non_loopback_host_does_not_get_loopback_only_protection(self) -> None:
        mcp = build_engine(EngineSpec(name="test", tools=()), host="0.0.0.0")
        assert mcp.settings.transport_security is None

    def test_explicit_transport_security_overrides_host_auto_detection(self) -> None:
        from mcp.server.transport_security import TransportSecuritySettings  # noqa: PLC0415

        explicit = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["host.docker.internal:*"],
        )
        mcp = build_engine(
            EngineSpec(name="test", tools=()),
            host="0.0.0.0",
            transport_security=explicit,
        )
        assert mcp.settings.transport_security == explicit


class TestExtendAndPinChatId:
    def test_extend_with_chat_id_accepts_room_id_alias(self) -> None:
        definition = TOOL_DEFINITIONS["band_send_message"]
        extended = extend_with_chat_id(definition.input_model, None)

        via_chat_id = extended.model_validate(
            {"content": "hi", "mentions": ["@x"], "chat_id": "r1"}
        )
        via_room_id = extended.model_validate(
            {"content": "hi", "mentions": ["@x"], "room_id": "r2"}
        )
        assert via_chat_id.chat_id == "r1"
        assert via_room_id.chat_id == "r2"

    def test_extend_with_chat_id_pinned_hides_field_from_schema(self) -> None:
        definition = TOOL_DEFINITIONS["band_send_message"]
        pinned = extend_with_chat_id(definition.input_model, "r_pinned")
        schema = pinned.model_json_schema()
        assert "chat_id" not in schema.get("properties", {})

    def test_pin_existing_chat_id_hides_field_from_schema(self) -> None:
        definition = TOOL_DEFINITIONS["band_send_my_chat_message"]
        pinned = pin_existing_chat_id(definition.input_model)
        schema = pinned.model_json_schema()
        assert "chat_id" not in schema.get("properties", {})


class TestSendEventWideInput:
    def test_advertises_all_five_message_types(self) -> None:
        schema = SendEventWideInput.model_json_schema()
        assert set(schema["properties"]["message_type"]["enum"]) == {
            "tool_call",
            "tool_result",
            "thought",
            "error",
            "task",
        }

    def test_accepts_tool_call_and_tool_result(self) -> None:
        for message_type in ("tool_call", "tool_result"):
            validated = SendEventWideInput.model_validate(
                {"content": "x", "message_type": message_type}
            )
            assert validated.message_type == message_type


class TestValidateUniqueToolNames:
    def test_raises_on_duplicate_across_registrations(self) -> None:
        definition = TOOL_DEFINITIONS["band_create_chatroom"]
        registration = build_tool_registration(
            definition,
            definition.input_model,
            resolver=_agent_resolver(FakeAgentTools()),
            strip_chat_id=False,
        )
        with pytest.raises(ValueError, match="Duplicate MCP tool names"):
            validate_unique_tool_names([registration, registration])


@pytest.fixture
async def agent_session_factory():
    """Yields a builder from a room-scoped FakeAgentTools to a connected
    ClientSession over a real (uniform-wrap, embedded-shaped) engine."""

    async def _build(fake: FakeAgentTools, *, definitions=None):
        resolver = _agent_resolver(fake)
        defs = definitions or [
            TOOL_DEFINITIONS[name]
            for name in (
                "band_send_message",
                "band_get_participants",
                "band_lookup_peers",
                "band_create_chatroom",
            )
        ]
        registrations = [
            build_tool_registration(
                definition,
                extend_with_chat_id(definition.input_model, None),
                resolver=resolver,
                strip_chat_id=True,
            )
            for definition in defs
        ]
        spec = EngineSpec(name="test-embedded", tools=tuple(registrations))
        return build_engine(spec)

    return _build


async def test_embedded_style_uniform_wrap_room_bound_dispatch(
    agent_session_factory,
) -> None:
    """Embedded's uniform wrap: even a CLI-room-less tool (create_chatroom)
    gets a chat_id field here, and it must be stripped before dispatch."""
    fake = FakeAgentTools(room_id="room-1")
    mcp = await agent_session_factory(fake)

    async with create_connected_server_and_client_session(mcp) as session:
        tool = await _list_tool(session, "band_create_chatroom")
        assert "chat_id" in tool.inputSchema["properties"]

        room_id = await _call(session, "band_create_chatroom", chat_id="room-1")
        assert room_id.startswith("room-")


async def test_embedded_send_message_round_trip_and_participant_refresh(
    agent_session_factory,
) -> None:
    fake = FakeAgentTools(
        room_id="room-1",
        participants=[{"id": "u1", "name": "Alice", "handle": "@alice"}],
    )
    mcp = await agent_session_factory(fake)

    async with create_connected_server_and_client_session(mcp) as session:
        result = await _call(
            session,
            "band_send_message",
            chat_id="room-1",
            content="hi",
            mentions=["@alice"],
        )
        assert result["content"] == "hi"
        assert fake.messages_sent == [
            {"id": "msg-0", "content": "hi", "mentions": ["@alice"]}
        ]


async def test_embedded_send_message_error_enriched_with_available_handles(
    agent_session_factory,
) -> None:
    fake = FakeAgentTools(
        room_id="room-1",
        participants=[{"id": "u1", "name": "Alice", "handle": "@alice"}],
    )
    mcp = await agent_session_factory(fake)

    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool(
            "band_send_message",
            {"chat_id": "room-1", "content": "hi", "mentions": []},
        )
        assert result.isError
        message = result.content[0].text
        assert "At least one mention is required" in message
        assert "@alice" in message


async def test_embedded_room_id_alias_routes_to_same_room(
    agent_session_factory,
) -> None:
    fake = FakeAgentTools(room_id="room-1")
    mcp = await agent_session_factory(fake)

    async with create_connected_server_and_client_session(mcp) as session:
        participants = await _call(session, "band_get_participants", room_id="room-1")
        assert participants == []


async def test_cli_style_pinned_agent_send_message_ignores_client_chat_id(
    agent_session_factory,
) -> None:
    """CLI-shaped pinning: the pin unconditionally overrides a client-sent
    chat_id (verified against registrar.py's original guarantee)."""
    fake = FakeAgentTools(room_id="room-pinned")
    resolver = _agent_resolver(fake)
    definition = TOOL_DEFINITIONS["band_send_message"]
    registration = build_tool_registration(
        definition,
        extend_with_chat_id(definition.input_model, "room-pinned"),
        resolver=resolver,
        strip_chat_id=True,
        pinned_room_id="room-pinned",
    )
    spec = EngineSpec(name="test-cli-pinned", tools=(registration,))
    mcp = build_engine(spec)

    async with create_connected_server_and_client_session(mcp) as session:
        tool = await _list_tool(session, "band_send_message")
        assert "chat_id" not in tool.inputSchema["properties"]

        result = await _call(
            session,
            "band_send_message",
            content="hi",
            mentions=["@bob"],
            chat_id="room-should-be-ignored",
        )
        assert result["content"] == "hi"


class _NoopHumanResolver:
    """Human-surface dispatch needs no per-room routing -- chat_id (if any)
    stays in ``arguments`` and is passed straight to the fake's method."""

    def __init__(self, human_tools: FakeHumanTools) -> None:
        self._human_tools = human_tools

    async def invoke(self, definition, chat_id, arguments):
        method = getattr(self._human_tools, definition.method_name)
        return await method(**arguments)


def _human_send_message_engine(fake: FakeHumanTools, *, pinned: bool) -> Any:
    definition = TOOL_DEFINITIONS["band_send_my_chat_message"]
    input_model = (
        pin_existing_chat_id(definition.input_model)
        if pinned
        else definition.input_model
    )
    registration = build_tool_registration(
        definition,
        input_model,
        resolver=_NoopHumanResolver(fake),
        strip_chat_id=False,
        pinned_room_id="chat-1" if pinned else None,
    )
    return build_engine(EngineSpec(name="test-human", tools=(registration,)))


async def test_human_room_bound_unpinned_keeps_chat_id_as_real_argument() -> None:
    fake = FakeHumanTools(
        chats=[{"id": "chat-1"}],
        chat_participants={"chat-1": [{"id": "p1", "name": "Alice"}]},
    )
    mcp = _human_send_message_engine(fake, pinned=False)

    async with create_connected_server_and_client_session(mcp) as session:
        tool = await _list_tool(session, "band_send_my_chat_message")
        assert "chat_id" in tool.inputSchema["properties"]

        await _call(
            session,
            "band_send_my_chat_message",
            chat_id="chat-1",
            content="hi",
            recipients="Alice",
        )
        assert fake.messages_sent[0]["chat_id"] == "chat-1"


async def test_human_room_bound_pinned_injects_and_hides_chat_id() -> None:
    fake = FakeHumanTools(
        chats=[{"id": "chat-1"}],
        chat_participants={"chat-1": [{"id": "p1", "name": "Alice"}]},
    )
    mcp = _human_send_message_engine(fake, pinned=True)

    async with create_connected_server_and_client_session(mcp) as session:
        tool = await _list_tool(session, "band_send_my_chat_message")
        assert "chat_id" not in tool.inputSchema["properties"]

        await _call(
            session,
            "band_send_my_chat_message",
            content="hi",
            recipients="Alice",
        )
        assert fake.messages_sent[0]["chat_id"] == "chat-1"


class EchoInput(BaseModel):
    """Echo a message back."""

    message: str = Field(..., description="Message to echo")


async def _echo(input_data: EchoInput) -> dict[str, str]:
    return {"echo": input_data.message}


async def test_custom_tool_room_bound_strips_chat_id_before_handler() -> None:
    seen: dict[str, Any] = {}

    async def handler(input_data: EchoInput) -> dict[str, str]:
        seen["message"] = input_data.message
        return {"echo": input_data.message}

    registration = build_custom_tool_registration(
        CustomToolSpec(input_model=EchoInput, handler=handler),
        room_bound=True,
    )
    spec = EngineSpec(name="test-custom", tools=(registration,))
    mcp = build_engine(spec)

    async with create_connected_server_and_client_session(mcp) as session:
        tool = await _list_tool(session, "echo")
        assert "chat_id" in tool.inputSchema["properties"]

        result = await _call(session, "echo", message="hi", chat_id="room-1")
        assert result == {"echo": "hi"}
        assert seen == {"message": "hi"}


async def test_custom_tool_accepts_bare_tuple_contract() -> None:
    """The bare (input_model, handler) tuple stays accepted -- the existing
    adapter contract, not deprecated by CustomToolSpec."""
    registration = build_custom_tool_registration((EchoInput, _echo))
    spec = EngineSpec(name="test-custom-tuple", tools=(registration,))
    mcp = build_engine(spec)

    async with create_connected_server_and_client_session(mcp) as session:
        result = await _call(session, "echo", message="hi")
        assert result == {"echo": "hi"}


async def test_custom_tool_default_factory_field_advertised_as_optional() -> None:
    """Regression: a field declared with ``Field(default_factory=...)`` (no
    literal ``default=``) must be advertised the same way Pydantic's own
    ``model_json_schema()`` advertises it -- optional, with no ``default`` key
    -- not marked required. ``field_info.default`` is Pydantic's
    ``PydanticUndefined`` sentinel for a factory-only field; passed through
    as a literal default it makes the synthesized handler signature's
    ``create_model()`` read "no default provided" and mark the field required."""

    class TagsInput(BaseModel):
        """Echo the given tags, defaulting to none."""

        tags: list[str] = Field(default_factory=list, description="tags to echo")

    async def handler(input_data: TagsInput) -> dict[str, list[str]]:
        return {"tags": input_data.tags}

    registration = build_custom_tool_registration(
        CustomToolSpec(input_model=TagsInput, handler=handler)
    )
    mcp = build_engine(EngineSpec(name="test-default-factory", tools=(registration,)))

    async with create_connected_server_and_client_session(mcp) as session:
        tool = await _list_tool(session, "tags")
        assert tool.inputSchema.get("required") in (None, [])
        assert "default" not in tool.inputSchema["properties"]["tags"]

        result = await _call(session, "tags")  # tags omitted entirely
        assert result == {"tags": []}


async def test_agent_multi_step_room_lifecycle(agent_session_factory) -> None:
    """One FakeAgentTools, one engine, three real dispatched calls in
    sequence: add_participant -> send_message (mentioning the participant
    that call just added) -> get_participants. Each step's assertion
    depends on the prior step's real mutated state, not a hardcoded id."""
    fake = FakeAgentTools(room_id="room-1")
    mcp = await agent_session_factory(
        fake,
        definitions=[
            TOOL_DEFINITIONS[name]
            for name in (
                "band_add_participant",
                "band_send_message",
                "band_get_participants",
            )
        ],
    )

    async with create_connected_server_and_client_session(mcp) as session:
        added = await _call(
            session, "band_add_participant", chat_id="room-1", identifier="@bob"
        )
        mention_handle = added["handle"]

        sent = await _call(
            session,
            "band_send_message",
            chat_id="room-1",
            content="welcome",
            mentions=[mention_handle],
        )
        assert sent["mentions"] == [mention_handle]

        participants = await _call(session, "band_get_participants", chat_id="room-1")
        assert any(p["id"] == added["id"] for p in participants)
        assert any(p["handle"] == mention_handle for p in participants)


class BootstrapInput(BaseModel):
    """Add a preset participant as part of custom session bootstrap."""

    identifier: str = Field(..., description="Participant identifier to add")


async def test_custom_tool_alongside_builtin_tools_in_one_session() -> None:
    """One EngineSpec registering a custom tool next to built-in agent
    tools, all bound to the same FakeAgentTools. The custom tool's handler
    calls straight through to the fake's real add_participant; a later
    built-in band_get_participants call is asserted against state that only
    makes sense if that mutation actually ran first."""
    fake = FakeAgentTools(room_id="room-1")
    resolver = _agent_resolver(fake)

    async def bootstrap(input_data: BootstrapInput) -> dict[str, str]:
        added = await fake.add_participant(input_data.identifier)
        return {"added_id": added["id"]}

    custom_registration = build_custom_tool_registration(
        CustomToolSpec(input_model=BootstrapInput, handler=bootstrap)
    )
    builtin_registrations = [
        build_tool_registration(
            definition,
            extend_with_chat_id(definition.input_model, None),
            resolver=resolver,
            strip_chat_id=True,
        )
        for definition in (
            TOOL_DEFINITIONS[name]
            for name in ("band_get_participants", "band_send_message")
        )
    ]
    spec = EngineSpec(
        name="test-custom-plus-builtin",
        tools=(custom_registration, *builtin_registrations),
    )
    mcp = build_engine(spec)

    async with create_connected_server_and_client_session(mcp) as session:
        bootstrapped = await _call(session, "bootstrap", identifier="@bob")

        participants = await _call(session, "band_get_participants", chat_id="room-1")
        assert any(p["id"] == bootstrapped["added_id"] for p in participants)

        sent = await _call(
            session,
            "band_send_message",
            chat_id="room-1",
            content="welcome",
            mentions=["@bob"],
        )
        assert sent["mentions"] == ["@bob"]


async def test_concurrent_dispatch_through_one_engine(monkeypatch) -> None:
    """Real dispatch through StandaloneResolver's full stack -- identity
    resolution, per-room caching, lock striping -- via the engine's own
    ``_tool_manager.call_tool``, not the resolver's internal method
    directly (mirrors test_shared.py's
    test_resolve_agent_id_concurrent_cold_start_issues_one_rest_call).
    ``shared_mod.AgentTools`` is patched to hand back a room-scoped
    FakeAgentTools instead of one backed by real REST calls, so the REST
    boundary stays fake while every dispatch/caching layer above it runs
    for real."""
    constructed: list[str] = []

    class RoomAgentTools(FakeAgentTools):
        def __init__(self, room_id: str, rest: object, agent_id: str | None = None):
            super().__init__(room_id=room_id)
            constructed.append(room_id)

    monkeypatch.setattr(shared_mod, "AgentTools", RoomAgentTools)

    identity = MagicMock()
    identity.data.id = "self-agent-id"

    async def slow_get_agent_me() -> MagicMock:
        await asyncio.sleep(0)
        return identity

    rest = MagicMock()
    rest.agent_api_identity.get_agent_me = AsyncMock(side_effect=slow_get_agent_me)
    resolver = StandaloneResolver(agent_rest=rest)
    mcp = build_engine(standalone_spec(Config(scope=["agent"], tools=[]), resolver))

    async def add_bob(room_id: str) -> None:
        await _direct_call(
            mcp, "band_add_participant", chat_id=room_id, identifier="@bob"
        )

    async def get_participants(room_id: str) -> list[dict[str, Any]]:
        return await _direct_call(mcp, "band_get_participants", chat_id=room_id)

    # room_A gets two concurrent cold hits (a mutation and a read); room_B
    # and room_C get one cold hit each -- a mix of repeated and distinct
    # rooms all cold-starting at once.
    await asyncio.gather(
        add_bob("room_A"),
        get_participants("room_A"),
        get_participants("room_B"),
        get_participants("room_C"),
    )

    # The agent's own identity is resolver-global (_resolve_agent_id's own
    # docstring: "resolved once, cached for the resolver's lifetime") --
    # resolved once regardless of how many distinct rooms cold-started
    # concurrently, let alone how many calls landed on each.
    assert rest.agent_api_identity.get_agent_me.await_count == 1
    # Each room's AgentTools construction is deduped by its lock stripe --
    # one instance per distinct room, not one per call that named it.
    assert sorted(constructed) == ["room_A", "room_B", "room_C"]
    assert len(resolver._agent_tools_cache) == 3

    room_a_participants = await get_participants("room_A")
    room_b_participants = await get_participants("room_B")
    assert any(p["handle"] == "@bob" for p in room_a_participants)
    assert room_b_participants == []  # no leakage from room_A's mutation

    # Cold-starting past the LRU cap still evicts down to the configured max.
    for i in range(AGENT_TOOLS_CACHE_MAX_SIZE):
        await get_participants(f"room_overflow_{i}")
    assert len(resolver._agent_tools_cache) == AGENT_TOOLS_CACHE_MAX_SIZE
