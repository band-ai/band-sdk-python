"""MCP customdispatch behavior."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel, Field

from band.integrations.mcp.engine import (
    CustomToolSpec,
    EngineSpec,
    build_custom_tool_registration,
    build_engine,
    build_tool_registration,
    extend_with_chat_id,
)
from band.runtime.custom_tools import declares_turn_effect
from band.runtime.tools import TOOL_DEFINITIONS, TurnEffect
from band.testing.fake_tools import FakeAgentTools
from tests.mcp.engine.models import BootstrapInput, EchoInput
from tests.mcp.engine.peers import BOB_PEER, _agent_resolver
from tests.mcp.engine.protocol import _call, _echo, _list_tool


class TestCustomDispatch:
    async def test_custom_tool_room_bound_strips_chat_id_before_handler(self) -> None:
        seen: dict[str, Any] = {}

        async def handler(input_data: EchoInput) -> dict[str, str]:
            seen["message"] = input_data.message
            return {"echo": input_data.message}

        fake = FakeAgentTools(room_id="room-1")
        registration = build_custom_tool_registration(
            CustomToolSpec(input_model=EchoInput, handler=handler),
            get_tools=lambda _chat_id: fake,
        )
        spec = EngineSpec(name="test-custom", tools=(registration,))
        mcp = build_engine(spec)

        async with create_connected_server_and_client_session(mcp) as session:
            tool = await _list_tool(session, "echo")
            assert "chat_id" in tool.inputSchema["properties"]

            result = await _call(session, "echo", message="hi", chat_id="room-1")
            assert result == {"echo": "hi"}
            assert seen == {"message": "hi"}

    async def test_room_bound_custom_tool_records_its_effect_on_the_resolved_room_turn(
        self,
    ) -> None:
        @declares_turn_effect(TurnEffect.REPLY)
        async def answer(input_data: EchoInput) -> dict[str, str]:
            return {"echo": input_data.message}

        rooms = {
            "room-1": FakeAgentTools(room_id="room-1"),
            "room-2": FakeAgentTools(room_id="room-2"),
        }
        registration = build_custom_tool_registration(
            CustomToolSpec(input_model=EchoInput, handler=answer),
            get_tools=rooms.get,
        )
        mcp = build_engine(EngineSpec(name="test-custom-effect", tools=(registration,)))

        async with create_connected_server_and_client_session(mcp) as session:
            await _call(session, "echo", message="hi", chat_id="room-2")

        assert {room: tools.turn.replied for room, tools in rooms.items()} == {
            "room-1": False,
            "room-2": True,
        }

    async def test_custom_tool_accepts_bare_tuple_contract(self) -> None:
        """The bare (input_model, handler) tuple stays accepted -- the existing
        adapter contract, not deprecated by CustomToolSpec."""
        registration = build_custom_tool_registration((EchoInput, _echo))
        spec = EngineSpec(name="test-custom-tuple", tools=(registration,))
        mcp = build_engine(spec)

        async with create_connected_server_and_client_session(mcp) as session:
            result = await _call(session, "echo", message="hi")
            assert result == {"echo": "hi"}

    async def test_custom_tool_default_factory_field_advertised_as_optional(
        self,
    ) -> None:
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
        mcp = build_engine(
            EngineSpec(name="test-default-factory", tools=(registration,))
        )

        async with create_connected_server_and_client_session(mcp) as session:
            tool = await _list_tool(session, "tags")
            assert tool.inputSchema.get("required") in (None, [])
            assert "default" not in tool.inputSchema["properties"]["tags"]

            result = await _call(session, "tags")  # tags omitted entirely
            assert result == {"tags": []}

    async def test_custom_tool_alongside_builtin_tools_in_one_session(self) -> None:
        """One EngineSpec registering a custom tool next to built-in agent
        tools, all bound to the same FakeAgentTools. The custom tool's handler
        calls straight through to the fake's real add_participant; a later
        built-in band_get_participants call is asserted against state that only
        makes sense if that mutation actually ran first."""
        fake = FakeAgentTools(room_id="room-1", peers=[BOB_PEER])
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

            participants = await _call(
                session, "band_get_participants", chat_id="room-1"
            )
            assert any(p["id"] == bootstrapped["added_id"] for p in participants)

            sent = await _call(
                session,
                "band_send_message",
                chat_id="room-1",
                content="welcome",
                mentions=["@bob"],
            )
            assert sent["success"] is True
            assert fake.messages_sent[-1]["mentions"] == ["@bob"]

    @pytest.mark.parametrize("field", ["chat_id", "room_id"])
    @pytest.mark.parametrize("room", ["", ".", "#", "room-1"])
    @pytest.mark.asyncio
    async def test_custom_tool_room_validation_precedes_handler(
        self, field: str, room: str
    ) -> None:
        class EchoInput(BaseModel):
            """Echo text."""

            text: str

        handled: list[str] = []

        async def echo(args: EchoInput) -> str:
            handled.append(args.text)
            return args.text

        registration = build_custom_tool_registration(
            CustomToolSpec(input_model=EchoInput, handler=echo),
            get_tools=lambda room: FakeAgentTools(room_id=room),
        )
        mcp = build_engine(
            EngineSpec(name="custom-room-validation", tools=(registration,))
        )
        async with create_connected_server_and_client_session(
            mcp._mcp_server
        ) as session:
            result = await session.call_tool(
                registration.name, {field: room, "text": "hello"}
            )
            assert bool(result.isError) is (room != "room-1")
            assert handled == (["hello"] if room == "room-1" else [])
