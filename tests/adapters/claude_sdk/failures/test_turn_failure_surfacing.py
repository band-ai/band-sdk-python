"""A failed or silent Claude SDK turn must surface a room-visible error, and
a turn that answered the room must stay quiet."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.claude_sdk import (
    APPROVAL_REQUESTED_TEMPLATE,
    APPROVAL_RESOLVED_TEMPLATE,
    TurnResultAlreadyReported,
)
from band.core.types import Emit
from tests.adapters.claude_sdk.fakecli import EndTurn, Raw, Thinking
from tests.adapters.claude_sdk.helpers import (
    MISSING_REPLY_TEXT,
    SEND_MESSAGE_MCP_NAME,
    ClaudeRoom,
)
from tests.baseline.decisions import ModelDecision

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def test_a_turn_that_answered_the_room_is_narrated_and_quiet(
    claude_room: OpenRoom,
) -> None:
    """Thinking, a lookup, then the reply: each step is narrated with the
    tool's bare name and outcome, and the answered turn reports nothing."""
    room = await claude_room(emit={Emit.TOOL_CALLS, Emit.THOUGHTS})
    room.claude.script(
        [
            Thinking("Check who is here first."),
            ModelDecision.call("mcp__band__band_get_participants", chat_id="room-1"),
            room.model_reply("Everyone is here."),
        ]
    )

    await room.send("Who is in this room?")

    assert room.chat == ["Everyone is here."]
    assert room.events.count("thought") == 1
    assert room.tool_errors == {
        "band_get_participants": False,
        "band_send_message": False,
    }
    assert room.failures == []


async def test_the_room_hears_whenever_a_turn_left_it_unanswered(
    claude_room: OpenRoom,
) -> None:
    """Plain text, lookups and status events alone, and a reply the platform
    rejected all leave the room unanswered; the session survives each one
    and the next real reply goes through quietly."""
    room = await claude_room()
    room.claude.script(
        [ModelDecision.text_reply("Here is my answer, in plain text.")],
        [
            ModelDecision.call("mcp__band__band_get_participants", chat_id="room-1"),
            ModelDecision.call(
                "mcp__band__band_send_event",
                chat_id="room-1",
                content="Still thinking",
                message_type="thought",
            ),
        ],
        [room.model_reply("This one bounces.")],
        [room.model_reply("This one lands.")],
    )

    for question in ("plain text?", "lookups only?"):
        with pytest.raises(TurnResultAlreadyReported):
            await room.send(question)
    room.tools.send_message_error = RuntimeError("network down")
    with pytest.raises(TurnResultAlreadyReported):
        await room.send("rejected reply?")
    room.tools.send_message_error = None
    await room.send("and now?")

    assert room.chat == ["This one lands."]
    assert room.failures == [MISSING_REPLY_TEXT] * 3
    assert len(room.claude.sessions) == 1


async def test_a_failure_the_cli_reports_reaches_the_room_with_its_status(
    claude_room: OpenRoom,
) -> None:
    """``is_error`` wins even when ``subtype`` says success and even after a
    reply went out; the room gets the CLI's own detail and API status."""
    room = await claude_room()
    room.claude.script(
        [EndTurn(is_error=True, result="Not logged in · Please run /login")],
        [
            room.model_reply("Partial answer."),
            EndTurn(
                is_error=True,
                errors=["authentication_error: invalid API key"],
                api_error_status=401,
            ),
        ],
    )

    for question in ("first", "second"):
        with pytest.raises(TurnResultAlreadyReported):
            await room.send(question)

    assert room.reported_failures == [
        {
            "provider": "claude_sdk",
            "code": None,
            "message": "Claude SDK turn failed: Not logged in · Please run /login",
            "detail": None,
        },
        {
            "provider": "claude_sdk",
            "code": "401",
            "message": (
                "Claude SDK turn failed: authentication_error: invalid API key "
                "(API status 401)"
            ),
            "detail": ["authentication_error: invalid API key"],
        },
    ]


async def test_a_declined_side_tool_never_explains_a_silent_turn(
    claude_room: OpenRoom,
) -> None:
    """Policy declines ``Bash`` and says so, yet the question is still
    unanswered, so the missing reply is reported; a turn that replies after
    the same decline stays quiet."""
    room = await claude_room(approval_mode="auto_decline")
    room.claude.script(
        [ModelDecision.call("Bash", command="rm -rf build")],
        [
            ModelDecision.call("Bash", command="git status"),
            room.model_reply("I can't clean the build, but here is the status."),
        ],
    )

    with pytest.raises(TurnResultAlreadyReported):
        await room.send("clean the build")
    await room.send("then just tell me the status")

    assert room.failures == [MISSING_REPLY_TEXT]
    assert room.chat[-1] == "I can't clean the build, but here is the status."
    assert sum("decline" in message for message in room.chat[:-1]) == 2


async def test_a_declined_reply_explains_the_silence_only_if_the_room_was_told(
    claude_room: OpenRoom,
) -> None:
    """A project that loads its settings can ask before every Band reply.
    Declining that reply in the room already tells the room why none came, so
    no missing reply is reported; when the approval prompt never reaches the
    room, the decline explains nothing and the missing reply is reported."""
    room = await claude_room(approval_mode="manual", setting_sources=["project"])
    room.claude.project_ask_rules = [SEND_MESSAGE_MCP_NAME]
    room.claude.script([room.model_reply("Here you go.")], [room.model_reply("Again.")])

    await room.send("answer me")
    await room.send("/decline a-1")
    await room.settled()
    room.tools.send_message_error = RuntimeError("network down")
    with pytest.raises(TurnResultAlreadyReported):
        await room.send("try once more")

    assert room.chat == [
        APPROVAL_REQUESTED_TEMPLATE.format(summary="band_send_message", token="a-1"),
        APPROVAL_RESOLVED_TEMPLATE.format(token="a-1", decision="decline"),
    ]
    assert room.failures == [MISSING_REPLY_TEXT]


async def test_tool_traffic_the_cli_carries_outside_assistant_calls_still_counts(
    claude_room: OpenRoom,
) -> None:
    """A subagent's nested reply arrives in a user envelope (with
    ``is_error`` omitted, which means success), and a result can ride in the
    assistant message itself; either one answers the turn."""
    room = await claude_room()
    room.claude.script(
        [
            Raw(
                {
                    "type": "user",
                    "parent_tool_use_id": "toolu_task",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_nested",
                                "name": SEND_MESSAGE_MCP_NAME,
                                "input": {},
                            },
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_nested",
                                "content": "sent",
                            },
                        ]
                    },
                }
            )
        ],
        [
            Raw(
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-fake",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_inline",
                                "name": SEND_MESSAGE_MCP_NAME,
                                "input": {},
                            },
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_inline",
                                "content": "sent",
                                "is_error": False,
                            },
                        ],
                    },
                }
            )
        ],
    )

    await room.send("delegate it")
    await room.send("answer inline")

    assert room.failures == []
