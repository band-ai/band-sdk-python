"""MCP agentdispatch behavior."""

from __future__ import annotations

import json

from mcp.shared.memory import create_connected_server_and_client_session

from band.integrations.mcp.engine import (
    EngineSpec,
    build_engine,
    build_tool_registration,
    extend_with_chat_id,
)
from band.runtime.tools import TOOL_DEFINITIONS
from band.testing.fake_tools import FakeAgentTools
from tests.mcp.engine.peers import ALICE_PARTICIPANT, BOB_PEER, _agent_resolver
from tests.mcp.engine.protocol import _call, _list_tool


class TestAgentDispatch:
    async def test_embedded_style_uniform_wrap_room_bound_dispatch(
        self,
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

    async def test_tool_results_are_plain_json_text_without_a_structured_wrapper(
        self,
        agent_session_factory,
    ) -> None:
        """No output schema and no ``{"result": "<json>"}`` structured content: a
        client that prefers structured content would show it double-encoded."""
        mcp = await agent_session_factory(FakeAgentTools(room_id="room-1"))

        async with create_connected_server_and_client_session(mcp) as session:
            tool = await _list_tool(session, "band_lookup_peers")
            result = await session.call_tool("band_lookup_peers", {"chat_id": "room-1"})

        assert tool.outputSchema is None
        assert result.structuredContent is None
        assert json.loads(result.content[0].text)["data"] == []

    async def test_embedded_send_message_round_trip_and_participant_refresh(
        self,
        agent_session_factory,
    ) -> None:
        fake = FakeAgentTools(room_id="room-1", participants=[ALICE_PARTICIPANT])
        mcp = await agent_session_factory(fake)

        async with create_connected_server_and_client_session(mcp) as session:
            result = await _call(
                session,
                "band_send_message",
                chat_id="room-1",
                content="hi",
                mentions=["@alice"],
            )
            assert result["success"] is True
            assert fake.messages_sent == [
                {"id": "msg-0", "content": "hi", "mentions": ["@alice"]}
            ]

    async def test_embedded_send_message_error_enriched_with_available_handles(
        self,
        agent_session_factory,
    ) -> None:
        fake = FakeAgentTools(room_id="room-1", participants=[ALICE_PARTICIPANT])
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
        self,
        agent_session_factory,
    ) -> None:
        fake = FakeAgentTools(room_id="room-1")
        mcp = await agent_session_factory(fake)

        async with create_connected_server_and_client_session(mcp) as session:
            participants = await _call(
                session, "band_get_participants", room_id="room-1"
            )
            assert participants == []

    async def test_cli_style_pinned_agent_send_message_ignores_client_chat_id(
        self,
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
            assert result["success"] is True

    async def test_agent_multi_step_room_lifecycle(self, agent_session_factory) -> None:
        """One FakeAgentTools, one engine, three real dispatched calls in
        sequence: add_participant -> send_message (mentioning the participant
        that call just added) -> get_participants. Each step's assertion
        depends on the prior step's real mutated state, not a hardcoded id."""
        fake = FakeAgentTools(room_id="room-1", peers=[BOB_PEER])
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

            sent = await _call(
                session,
                "band_send_message",
                chat_id="room-1",
                content="welcome",
                mentions=[added["id"]],
            )
            assert sent["success"] is True
            assert fake.messages_sent[-1]["mentions"] == [added["id"]]

            participants = await _call(
                session, "band_get_participants", chat_id="room-1"
            )
            assert any(p["id"] == added["id"] for p in participants)
            assert any(p["handle"] == "bob" for p in participants)
