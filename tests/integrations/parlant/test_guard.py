"""Tests for the failure handling every Parlant tool call goes through."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from band.core.exceptions import BandToolError
from band.integrations.parlant.tools import create_parlant_tools, set_session_tools
from tests.integrations.parlant.helpers import SESSION_ID

pytest.importorskip("parlant.sdk")  # real @p.tool schemas; dev-parlant venv only

# The guard module imports parlant, so its logger is named rather than imported.
GUARD_LOGGER = "band.integrations.parlant.guard"


class RecordInput(BaseModel):
    """Store a record."""

    count: int


async def store_upstream_record(args: RecordInput) -> str:
    """Fails validating an upstream payload: a handler bug, not a bad argument."""
    try:
        RecordInput.model_validate({"count": "many"})
    except ValidationError as exc:
        raise RuntimeError("upstream returned a malformed record") from exc
    return "stored"


@pytest.fixture
def guarded_tools() -> dict[str, Any]:
    """The Band tools plus one custom tool, each by name as its guarded function."""
    entries = create_parlant_tools(custom_tools=[(RecordInput, store_upstream_record)])
    return {entry.tool.name: entry.function for entry in entries}


def lines_at(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    """The captured log messages logged at exactly *level*."""
    return [record.getMessage() for record in caplog.records if record.levelno == level]


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
    async def test_logs_the_outcome_at_info_and_room_content_at_debug(
        self, parlant_tools, mock_tools, mock_context, caplog
    ):
        """Operators grep the INFO lines for a per-call outcome; the call's
        arguments and result are room content, so they appear only at DEBUG."""
        set_session_tools(mock_context.session_id, mock_tools)
        send_message = parlant_tools["band_send_message"]

        with caplog.at_level(logging.DEBUG, logger=GUARD_LOGGER):
            await send_message(mock_context, "Hello", "Alice")

        assert lines_at(caplog, logging.INFO) == [
            f"[Parlant Tool] band_send_message called: session={SESSION_ID}",
            "[Parlant Tool] band_send_message completed",
        ]
        debug = "\n".join(lines_at(caplog, logging.DEBUG))
        assert "content=Hello" in debug
        assert "Message sent to Alice" in debug

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool", "arguments", "level", "traceback"),
        [
            (
                "band_get_task",
                {"id": "task-1", "include": "bogus"},
                logging.WARNING,
                False,
            ),
            ("record", {"count": "many"}, logging.WARNING, False),
            ("record", {"count": "1"}, logging.ERROR, True),
        ],
        ids=[
            "band-tool-invalid-argument",
            "custom-tool-invalid-argument",
            "handler-failure-chaining-a-validation-error",
        ],
    )
    async def test_only_invalid_arguments_log_a_warning_without_a_traceback(
        self,
        guarded_tools,
        mock_tools,
        mock_context,
        caplog,
        tool,
        arguments,
        level,
        traceback,
    ):
        """A bad argument is the model's to fix from the returned error, not an
        operator alert; any other failure is an error with its traceback."""
        set_session_tools(mock_context.session_id, mock_tools)

        with caplog.at_level(logging.WARNING, logger=GUARD_LOGGER):
            result = await guarded_tools[tool](mock_context, **arguments)

        assert result.data.startswith("Error ")
        [record] = caplog.records
        assert (record.levelno, bool(record.exc_info)) == (level, traceback)
