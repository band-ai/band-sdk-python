from __future__ import annotations

import pytest

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
    LettaMCPConfig,
)
from band.converters.letta import LettaSessionState
from band.testing import (
    FakeAgentTools,
)
from tests.adapters.lettakit import (
    make_assistant_message,
    make_letta_response,
    make_platform_message,
    make_tool_call_message,
    make_tool_return_message,
    ready_letta_adapter,
)


class TestSendToolResolution:
    def test_resolves_external_band_mcp_names(self) -> None:
        adapter = LettaAdapter()
        adapter._mcp.resolve_send_tools(
            ["create_agent_chat_message", "create_agent_chat_event", "health_check"]
        )
        assert adapter._mcp.send_message_tool == "create_agent_chat_message"
        assert adapter._mcp.send_event_tool == "create_agent_chat_event"
        assert adapter._mcp.silent_reporting_tools == {
            "create_agent_chat_message",
            "create_agent_chat_event",
        }

    def test_prefers_band_names_when_both_present(self) -> None:
        adapter = LettaAdapter()
        adapter._mcp.resolve_send_tools(
            ["band_send_message", "create_agent_chat_message", "band_send_event"]
        )
        assert adapter._mcp.send_message_tool == "band_send_message"
        assert adapter._mcp.send_event_tool == "band_send_event"

    def test_falls_back_to_band_names_when_none_discovered(self) -> None:
        adapter = LettaAdapter()
        adapter._mcp.resolve_send_tools(["some_unrelated_tool"])
        assert adapter._mcp.send_message_tool == "band_send_message"
        assert adapter._mcp.send_event_tool == "band_send_event"

    def test_enforcement_prompt_uses_resolved_names(self) -> None:
        adapter = LettaAdapter()
        adapter._system_prompt = "Base prompt"
        adapter._mcp.resolve_send_tools(
            ["create_agent_chat_message", "create_agent_chat_event"]
        )
        text = adapter._instruction_text("room-1")
        assert "create_agent_chat_message" in text
        assert "band_send_message" not in text

    @pytest.mark.asyncio
    async def test_relay_detection_uses_resolved_name(self) -> None:
        """A send via the resolved (external) tool name suppresses auto-relay."""
        adapter, mock_client = ready_letta_adapter(
            LettaAdapterConfig(mcp=LettaMCPConfig(mode="external"))
        )
        adapter._mcp.resolve_send_tools(
            ["create_agent_chat_message", "create_agent_chat_event"]
        )
        mock_client.agents.messages.create.return_value = make_letta_response(
            make_tool_call_message("create_agent_chat_message"),
            make_tool_return_message("create_agent_chat_message"),
            make_assistant_message("Done!"),
        )

        tools = FakeAgentTools()
        await adapter.on_message(
            make_platform_message(),
            tools,
            LettaSessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert len(tools.messages_sent) == 0
