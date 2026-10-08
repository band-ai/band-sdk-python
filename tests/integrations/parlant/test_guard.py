"""Tests for the failure handling every Parlant tool call goes through."""

from __future__ import annotations

import pytest

from band.core.exceptions import BandToolError
from band.integrations.parlant.tools import set_session_tools

pytest.importorskip("parlant.sdk")  # real @p.tool schemas; dev-parlant venv only


class TestGuardFailures:
    """Exercised through ``band_send_message``, a real guarded tool."""

    @pytest.mark.asyncio
    async def test_send_message_returns_error_without_tools(
        self, parlant_tools, mock_context
    ):
        """Should return error when no tools available."""
        send_message = parlant_tools["band_send_message"]
        result = await send_message(mock_context, "Hello", "Alice")

        assert "Error: No tools available" in result.data

    @pytest.mark.asyncio
    async def test_send_message_translates_band_tool_error(
        self, parlant_tools, mock_tools, mock_context
    ):
        """BandToolError from underlying tool must surface as ToolResult, not crash.

        Pins the wrapper translation contract: framework wrappers must catch
        BandToolError raised by AgentTools and return a model-visible
        failure value so the LLM can recover, instead of letting the exception
        crash the turn.
        """
        mock_tools.send_message.side_effect = BandToolError(
            "Backend rejected message: 503 Service Unavailable"
        )
        set_session_tools(mock_context.session_id, mock_tools)

        send_message = parlant_tools["band_send_message"]
        # Must NOT raise — wrapper translates the exception to a tool failure
        result = await send_message(mock_context, "Hello", "Alice")

        # Result is a ToolResult with the error text visible to the LLM
        assert "Error sending message" in result.data
        assert "503" in result.data

    @pytest.mark.asyncio
    async def test_send_message_mention_hint_survives_session_teardown_race(
        self, parlant_tools, mock_tools, mock_context
    ):
        """A room torn down between the tool body's own lookup and the
        mention-hint failure handler's re-lookup must not crash the call.

        guard_failures re-fetches session tools independently when building
        the mention hint; if the session vanished in between, that re-fetch
        returns None and must fall back to the plain error, not attribute
        error out on None.participants.
        """
        set_session_tools(mock_context.session_id, mock_tools)

        def _fail_and_tear_down_session(*args, **kwargs):
            set_session_tools(mock_context.session_id, None)
            raise BandToolError("Backend rejected message: 503 Service Unavailable")

        mock_tools.send_message.side_effect = _fail_and_tear_down_session

        send_message = parlant_tools["band_send_message"]
        # Must NOT raise AttributeError from None.participants
        result = await send_message(mock_context, "Hello", "Alice")

        assert "Error sending message" in result.data
        assert "503" in result.data

    @pytest.mark.asyncio
    async def test_tool_handles_exception(
        self, parlant_tools, mock_tools, mock_context
    ):
        """Should return error message when tool raises exception."""
        mock_tools.send_message.side_effect = Exception("Connection failed")
        set_session_tools(mock_context.session_id, mock_tools)

        send_message = parlant_tools["band_send_message"]
        result = await send_message(mock_context, "Hello", "Alice")

        assert "Error sending message: Connection failed" in result.data

    @pytest.mark.asyncio
    async def test_tool_returns_error_on_malformed_call_arguments(
        self, parlant_tools, mock_context
    ):
        """A signature mismatch in guard_failures's own bind() step -- before
        the call-handling try starts, so there's no call.arguments yet to
        build the usual failure message from -- must return a ToolResult,
        not propagate a raw TypeError out of the tool coroutine."""
        send_message = parlant_tools["band_send_message"]

        result = await send_message(mock_context)  # missing content/mentions

        assert result.data.startswith("Error calling band_send_message:")

    @pytest.mark.asyncio
    async def test_tool_logs_result_on_success(
        self, parlant_tools, mock_tools, mock_context, caplog
    ):
        """guard_failures must log a per-call outcome, not just the initial
        'called' line -- operators grep these logs for tool-level
        confirmation (e.g. that a specific branch was hit)."""
        set_session_tools(mock_context.session_id, mock_tools)
        send_message = parlant_tools["band_send_message"]

        with caplog.at_level("INFO"):
            await send_message(mock_context, "Hello", "Alice")

        result_logs = [
            r
            for r in caplog.records
            if r.getMessage().startswith("[Parlant Tool] band_send_message ->")
        ]
        assert len(result_logs) == 1
