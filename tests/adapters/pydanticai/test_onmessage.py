"""OnMessage behavior for Pydantic AI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from band.adapters.pydantic_ai import PydanticAIAdapter, PydanticAIAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.testing import MISSING_REPLY_FAILURE, FakeAgentTools, failure_reports
from tests.adapters.pydanticai.kit import make_stream_events
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input


class TestOnMessage:
    """Tests for on_message() method."""

    @pytest.mark.asyncio
    async def test_initializes_history_on_bootstrap(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should initialize room history on first message."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        result_messages = [ModelRequest(parts=[UserPromptPart(content="test")])]
        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=result_messages,
                tool_results=[("band_send_message", "Message sent", "call-1")],
            )
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        assert "room-123" in adapter._message_history

    @pytest.mark.asyncio
    async def test_loads_existing_history(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should load historical messages on bootstrap."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        existing_history = [
            ModelRequest(parts=[UserPromptPart(content="[Bob]: Previous message")]),
            ModelResponse(parts=[TextPart(content="Previous response")]),
        ]

        result_messages = existing_history + [
            ModelRequest(parts=[UserPromptPart(content="new")])
        ]
        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=result_messages,
                tool_results=[("band_send_message", "Message sent", "call-1")],
            )
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=existing_history,
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Verify history was passed to agent.run_stream_events()
        call_kwargs = adapter._agent.run_stream_events.call_args.kwargs
        assert "message_history" in call_kwargs
        assert len(call_kwargs["message_history"]) == 2

    @pytest.mark.asyncio
    async def test_injects_participants_message(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """Should inject participants update when provided."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))

        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(
                result_messages=[],
                tool_results=[("band_send_message", "Message sent", "call-1")],
            )
        )

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=[],
            participants_msg="Alice joined the room",
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Check that participant message was added to history before run
        call_kwargs = adapter._agent.run_stream_events.call_args.kwargs
        message_history = call_kwargs.get("message_history", [])
        # First message should be the participant update
        if message_history:
            first_msg = message_history[0]
            assert isinstance(first_msg, ModelRequest)
            assert "[System]: Alice joined" in first_msg.parts[0].content

    @pytest.mark.asyncio
    async def test_creates_agent_lazily_if_not_started(
        self, sample_message, mock_tools
    ):
        """Should create agent lazily if on_started wasn't called."""
        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(
                model="openai:gpt-5.4", custom_section="Test section"
            )
        )
        # Don't call on_started - set agent_name directly for prompt rendering
        adapter.agent_name = "LazyBot"

        with patch.object(adapter, "_create_agent") as mock_create:
            mock_agent = MagicMock()
            mock_agent.run_stream_events = MagicMock(
                return_value=make_stream_events(
                    result_messages=[],
                    tool_results=[("band_send_message", "Message sent", "call-1")],
                )
            )
            mock_create.return_value = mock_agent

            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_turn_that_never_replied_is_reported_by_the_verdict(
        self, mock_pydantic_agent
    ):
        """A clean run that never replied, declined or acted is a silently
        dropped turn — the shared verdict reports it, even without an exception."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))
        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")
        adapter._agent.run_stream_events = MagicMock(
            return_value=make_stream_events(result_messages=[])
        )
        tools = FakeAgentTools(room_id=ROOM_ID)

        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(turn_input(tools))

        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
