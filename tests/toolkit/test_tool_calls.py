"""Exact tool allowlists over persisted tool-call transcripts."""

from __future__ import annotations

import json

import pytest
from band_rest import ChatMessage

from band.core.types import MessageType
from band.runtime.tools.types import BandTool
from tests.e2e.baseline.toolkit.observations.tool_calls import ToolCall, ToolCalls


@pytest.fixture
def reply_call() -> ToolCall:
    message = ChatMessage(
        id="reply-call",
        sender_id="agent",
        sender_type="Agent",
        message_type=MessageType.TOOL_CALL,
        content=json.dumps(
            {
                "name": BandTool.SEND_MESSAGE,
                "args": {"content": "@user ROOM0B39D71DC", "mentions": ["user"]},
                "tool_call_id": "call-reply",
            }
        ),
    )
    call = ToolCall.from_event(message)
    assert call is not None
    return call


@pytest.mark.parametrize("reply_count", [1, 2])
def test_reply_delivery_is_allowed(reply_call: ToolCall, reply_count: int) -> None:
    calls = ToolCalls([reply_call] * reply_count)

    calls.assert_only(BandTool.SEND_MESSAGE)


@pytest.mark.parametrize(
    "forbidden",
    [
        "read_file",
        BandTool.READ_ROOM_FILE,
        BandTool.GET_MEMORY,
        BandTool.LIST_MEMORIES,
        BandTool.SEND_EVENT,
        BandTool.SEND_MESSAGE.upper(),
        f"{BandTool.SEND_MESSAGE}_lookalike",
    ],
)
def test_reply_does_not_hide_forbidden_calls(
    reply_call: ToolCall, forbidden: str
) -> None:
    calls = ToolCalls([ToolCall(name=forbidden), reply_call])

    with pytest.raises(AssertionError, match="unexpected tools") as failure:
        calls.assert_only(BandTool.SEND_MESSAGE)

    assert forbidden in str(failure.value)


def test_missing_calls_cannot_pass_the_allowlist() -> None:
    with pytest.raises(AssertionError, match="no tool calls"):
        ToolCalls().assert_only(BandTool.SEND_MESSAGE)
