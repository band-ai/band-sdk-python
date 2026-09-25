from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.converters.claude_sdk import ClaudeSDKSessionState
from band.integrations.claude_sdk.dedup_tools import DedupingAgentTools


class TestOnMessage:
    """Tests for on_message() method (bootstrap, history, invoke and response)."""

    @pytest.mark.asyncio
    async def test_initializes_history_on_bootstrap(self, sample_message, mock_tools):
        """First message in a room initializes session context and triggers invoke."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(
                adapter, "_process_response", new_callable=AsyncMock
            ) as mock_process,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            # By default the adapter wraps tools with DedupingAgentTools so
            # MCP tool calls go through the dedup shim.  The wrapped
            # instance is what gets stored and forwarded.

            stored_tools = adapter._room_tools["room-123"]
            assert isinstance(stored_tools, DedupingAgentTools)
            assert stored_tools._inner is mock_tools
            assert adapter._session_context["room-123"] == ""
            mock_manager.get_or_create_session.assert_awaited_once_with(
                "room-123", resume_session_id=None
            )
            mock_client.query.assert_awaited_once()
            mock_process.assert_awaited_once_with(mock_client, "room-123", stored_tools)

    @pytest.mark.asyncio
    async def test_loads_existing_history_on_bootstrap(
        self, sample_message, mock_tools
    ):
        """When history is provided on bootstrap, it is loaded and used for the next invoke."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)
        prior_context = "[Alice]: Hello\n[Bot]: Hi there."

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=prior_context),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            assert adapter._session_context["room-123"] == prior_context
            call_args = mock_client.query.call_args[0][0]
            # History is framed as the agent's own memory (authoritative), not a
            # passive quote, so the model recalls facts from it under the coding preset.
            assert "memory of this room" in call_args
            assert prior_context in call_args

    @pytest.mark.asyncio
    async def test_invoke_and_response(self, sample_message, mock_tools):
        """Adapter invokes the SDK client and processes response."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(return_value=mock_client)

        with (
            patch(
                "band.adapters.claude_sdk.ClaudeSessionManager",
                return_value=mock_manager,
            ),
            patch.object(
                adapter, "_process_response", new_callable=AsyncMock
            ) as mock_process,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text=""),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            mock_client.query.assert_awaited_once()
            full_message = mock_client.query.call_args[0][0]
            assert "room-123" in full_message
            assert "Hello, agent!" in full_message
            mock_process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refuses_second_message_while_a_turn_is_running(
        self, sample_message, mock_tools
    ):
        """A room with a still-running turn is refused, not queued or double-started."""
        adapter = ClaudeSDKAdapter()
        adapter._session_manager = AsyncMock()
        never_release = asyncio.Event()
        running_turn = asyncio.create_task(never_release.wait())
        adapter._turn_tasks["room-123"] = running_turn

        await adapter.on_message(
            msg=sample_message,
            tools=mock_tools,
            history=ClaudeSDKSessionState(text=""),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        adapter._session_manager.get_or_create_session.assert_not_awaited()
        assert adapter._turn_tasks["room-123"] is running_turn
        mock_tools.send_message.assert_awaited_once()
        assert (
            mock_tools.send_message.call_args[0][0]
            == "Still processing the previous request in this room."
        )
        assert mock_tools.send_message.call_args[0][1] == ["user-456"]

        never_release.set()
        await running_turn
