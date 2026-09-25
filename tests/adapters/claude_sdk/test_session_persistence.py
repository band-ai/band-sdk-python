from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.converters.claude_sdk import ClaudeSDKSessionState
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from tests.adapters.claude_sdk.helpers import (
    SEND_MESSAGE_MCP_NAME,
    result_message,
    tool_turn,
)


class TestSessionPersistence:
    """Tests for session persistence via task events."""

    @pytest.mark.asyncio
    async def test_emits_task_event_after_session_id_capture(self, mock_tools):
        """Should emit task event with session_id after ResultMessage."""
        # emit=() isolates the session task event, which posts unconditionally
        # regardless of emit (see _persist_session_id) — narration is opt-out
        # by default and would otherwise add tool_call/tool_result events too.
        adapter = ClaudeSDKAdapter(emit=())

        # A turn that actually replied via band_send_message, so the missing-reply
        # guard stays quiet and the only send_event call is the session task event.
        turn = tool_turn(SEND_MESSAGE_MCP_NAME)
        result_msg = result_message(session_id="sess-xyz-789")

        mock_client = MagicMock()

        async def mock_receive():
            for sdk_message in turn:
                yield sdk_message
            yield result_msg

        mock_client.receive_response = mock_receive

        await adapter._process_response(mock_client, "room-123", mock_tools)

        # Verify task event was emitted
        mock_tools.send_event.assert_called_once_with(
            content="Claude SDK session",
            message_type="task",
            metadata={"claude_sdk_session_id": "sess-xyz-789"},
        )
        # Verify in-memory cache was updated
        assert adapter._session_ids["room-123"] == "sess-xyz-789"

    @pytest.mark.asyncio
    async def test_uses_history_session_id_for_resume(self, sample_message, mock_tools):
        """Should use history.session_id for resume on bootstrap."""
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
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(
                    text="[Alice]: Hello", session_id="sess-from-history"
                ),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            mock_manager.get_or_create_session.assert_awaited_once_with(
                "room-123", resume_session_id="sess-from-history"
            )

    @pytest.mark.asyncio
    async def test_no_resume_on_non_bootstrap(self, sample_message, mock_tools):
        """Should not attempt resume on non-bootstrap messages."""
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
            patch.object(adapter, "_process_response", new_callable=AsyncMock),
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(
                    text="", session_id="sess-should-not-use"
                ),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-123",
            )

            mock_manager.get_or_create_session.assert_awaited_once_with(
                "room-123", resume_session_id=None
            )

    @pytest.mark.asyncio
    async def test_falls_back_to_new_session_on_resume_failure(
        self, sample_message, mock_tools
    ):
        """Should create new session if resume fails."""
        adapter = ClaudeSDKAdapter()
        mock_client = MagicMock()
        mock_client.query = AsyncMock()
        mock_manager = AsyncMock()
        # First call (with resume) fails, second call (without) succeeds
        mock_manager.get_or_create_session = AsyncMock(
            side_effect=[Exception("Resume failed"), mock_client]
        )

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
            # Should not raise — falls back to new session
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=ClaudeSDKSessionState(text="", session_id="sess-broken"),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

            assert mock_manager.get_or_create_session.await_count == 2
            # Second call should be without resume
            second_call = mock_manager.get_or_create_session.call_args_list[1]
            assert second_call == (("room-123",), {"resume_session_id": None})
            # A self-healed retry is not a reportable failure.
            mock_tools.send_failure.assert_not_called()

    @pytest.mark.asyncio
    async def test_reports_error_when_no_stored_session_to_retry(
        self, sample_message, mock_tools
    ):
        """No stored session id means there is nothing to fall back to, so
        the failure must surface without leaking the raw exception text."""
        adapter = ClaudeSDKAdapter()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(
            side_effect=Exception("Session setup failed")
        )

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager",
            return_value=mock_manager,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            with pytest.raises(Exception, match="Session setup failed"):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=ClaudeSDKSessionState(text=""),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

        mock_tools.send_failure.assert_called_once()
        failure = mock_tools.send_failure.call_args.args[0]
        assert failure.provider == "claude_sdk"
        assert failure.message == GENERIC_PROVIDER_FAILURE_MESSAGE
        assert "Session setup failed" not in failure.message

    @pytest.mark.asyncio
    async def test_reports_error_when_fallback_session_also_fails(
        self, sample_message, mock_tools
    ):
        """A failure in the fallback session-creation attempt must surface
        without leaking the raw exception text."""
        adapter = ClaudeSDKAdapter()
        mock_manager = AsyncMock()
        mock_manager.get_or_create_session = AsyncMock(
            side_effect=[Exception("Resume failed"), Exception("Fresh session failed")]
        )

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager",
            return_value=mock_manager,
        ):
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            with pytest.raises(Exception, match="Fresh session failed"):
                await adapter.on_message(
                    msg=sample_message,
                    tools=mock_tools,
                    history=ClaudeSDKSessionState(text="", session_id="sess-broken"),
                    participants_msg=None,
                    contacts_msg=None,
                    is_session_bootstrap=True,
                    room_id="room-123",
                )

        mock_tools.send_failure.assert_called_once()
        failure = mock_tools.send_failure.call_args.args[0]
        assert failure.provider == "claude_sdk"
        assert failure.message == GENERIC_PROVIDER_FAILURE_MESSAGE
        assert "Fresh session failed" not in failure.message

    @pytest.mark.asyncio
    async def test_task_event_failure_does_not_break_flow(self, mock_tools):
        """Task event emission failure should not break the message flow."""
        adapter = ClaudeSDKAdapter()
        mock_tools.send_event = AsyncMock(side_effect=Exception("Network error"))

        turn = tool_turn(SEND_MESSAGE_MCP_NAME)
        result_msg = result_message(session_id="sess-xyz")

        mock_client = MagicMock()

        async def mock_receive():
            for sdk_message in turn:
                yield sdk_message
            yield result_msg

        mock_client.receive_response = mock_receive

        # Should not raise despite send_event failure
        await adapter._process_response(mock_client, "room-123", mock_tools)

        # Session ID should still be captured in-memory
        assert adapter._session_ids["room-123"] == "sess-xyz"
