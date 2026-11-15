"""The Band MCP tool registrations an agent's tools build, built-in and custom."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from band_rest import ListAgentChatParticipantsResponse

from band.core.exceptions import BandToolError
from band.integrations.mcp.engine import (
    build_band_mcp_tool_registrations,
    build_resolved_band_mcp_tool_registrations,
)
from band.runtime.custom_tools import get_custom_tool_name
from band.runtime.tools import AgentTools
from tests.integrations.mcp.localserver.kit import (
    EchoInput,
    echo_tool,
    registration_named,
)


class TestBuildBandMcpToolRegistrations:
    def test_includes_builtin_and_custom_tools(self) -> None:
        agent_tools = AgentTools("room-123", MagicMock(), [])

        registrations = build_band_mcp_tool_registrations(
            agent_tools,
            additional_tools=[(EchoInput, echo_tool)],
        )

        tool_names = {registration.name for registration in registrations}
        assert "band_send_message" in tool_names
        assert get_custom_tool_name(EchoInput) in tool_names

    def test_rejects_duplicate_tool_names(self) -> None:
        agent_tools = AgentTools("room-123", MagicMock(), [])

        with pytest.raises(ValueError, match="Duplicate MCP tool names"):
            build_band_mcp_tool_registrations(
                agent_tools,
                additional_tools=[
                    (EchoInput, echo_tool),
                    (EchoInput, echo_tool),
                ],
            )

    @pytest.mark.asyncio
    async def test_resolved_registrations_advertise_chat_id(self) -> None:
        """The embedded door's uniform wrap advertises ``chat_id`` (the
        canonical name); ``room_id`` remains an accepted input alias only --
        see test_resolved_registrations_dispatch_by_room_id below."""
        tools_by_room = {
            "room-123": AgentTools("room-123", MagicMock(), []),
        }
        registrations = build_resolved_band_mcp_tool_registrations(
            get_tools=tools_by_room.get
        )

        registration = registration_named(registrations, "band_get_participants")
        schema = registration.input_model.model_json_schema()

        assert "chat_id" in schema["properties"]
        assert "chat_id" in schema["required"]
        assert "room_id" not in schema["properties"]

    @pytest.mark.asyncio
    async def test_resolved_registrations_dispatch_by_room_id(self) -> None:
        rest = MagicMock()
        rest.agent_api_participants = MagicMock()
        rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
            return_value=ListAgentChatParticipantsResponse(data=[])
        )

        room_tools = AgentTools("room-123", rest, [])
        registrations = build_resolved_band_mcp_tool_registrations(
            get_tools={"room-123": room_tools}.get
        )
        registration = registration_named(registrations, "band_get_participants")

        await registration.execute({"room_id": "room-123"})

        rest.agent_api_participants.list_agent_chat_participants.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resolved_send_message_errors_include_available_handles(
        self,
    ) -> None:
        room_tools = AgentTools(
            "room-123",
            MagicMock(),
            [
                {"id": "user-1", "name": "Alice", "handle": "@alice"},
                {"id": "self", "name": "Self", "handle": "@self"},
            ],
            agent_id="self",
        )
        registrations = build_resolved_band_mcp_tool_registrations(
            get_tools={"room-123": room_tools}.get
        )
        registration = registration_named(registrations, "band_send_message")

        with pytest.raises(BandToolError) as exc_info:
            await registration.execute(
                {"room_id": "room-123", "content": "hello", "mentions": []}
            )

        message = str(exc_info.value)
        assert "At least one mention is required" in message
        assert "@alice" in message
        assert "@self" not in message
        # The enricher must not re-append handles that the error already carries.
        assert message.count("Available handles:") == 1
        assert message.count("@alice") == 1
