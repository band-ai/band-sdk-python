"""An idle room's Claude Code process is released; its next turn resumes the session."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk import CLIConnectionError

from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from tests.adapters.claude_sdk.fakecli import Hangup, Hold
from tests.adapters.claude_sdk.helpers import MEMORY_FRAMING, ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]

EARLIER_FACT = "My favorite color is teal."
QUESTION = "What is my favorite color?"


def transcript_item(msg_id: str, content: str) -> dict[str, Any]:
    return {
        "id": msg_id,
        "content": content,
        "sender_id": "u1",
        "sender_type": "User",
        "sender_name": "Bob",
        "message_type": "text",
    }


def room_transcript(*, question_id: str) -> list[dict[str, Any]]:
    """The room as the platform holds it: the first message, then the question."""
    return [
        transcript_item("msg-1", EARLIER_FACT),
        transcript_item(question_id, QUESTION),
    ]


async def released_room(claude_room: OpenRoom, workspace: Path) -> ClaudeRoom:
    """A room that answered one message, then had its idle Claude process released."""
    room = await claude_room(workspace_for_room=lambda _room_id: str(workspace))
    room.claude.script([room.model_reply("Noted.")])
    await room.send(EARLIER_FACT)
    await room.settled()
    await room.adapter.release_room_resources(room.room_id)
    return room


async def test_a_released_room_resumes_its_session_in_the_same_workspace(
    claude_room: OpenRoom, tmp_path: Path
) -> None:
    room = await released_room(claude_room, tmp_path)
    room.claude.script([room.model_reply("Teal.")])

    await room.send(QUESTION)

    released, resumed = room.claude.sessions
    assert not released.alive
    assert room.claude.resumed == [None, released.session_id]
    assert str(resumed.options.cwd) == str(released.options.cwd) == str(tmp_path)
    assert MEMORY_FRAMING not in room.claude.prompts[-1]


async def test_a_session_that_cannot_resume_replays_the_fetched_room_history(
    claude_room: OpenRoom, tmp_path: Path
) -> None:
    room = await released_room(claude_room, tmp_path)
    [released] = room.claude.sessions
    room.claude.unresumable.add(released.session_id)
    room.tools.set_room_context(room_transcript(question_id="msg-2"))
    room.claude.script([room.model_reply("Teal.")])

    await room.send(QUESTION)

    assert room.claude.resumed == [None, released.session_id, None]
    memory, live = room.claude.prompts[-1].split("\n\n")
    assert (EARLIER_FACT in memory, QUESTION in memory) == (True, False)
    assert QUESTION in live
    assert room.failures == []


async def test_a_room_whose_history_cannot_be_fetched_fails_the_turn_and_retries(
    claude_room: OpenRoom, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    room = await released_room(claude_room, tmp_path)
    [released] = room.claude.sessions
    room.claude.unresumable.add(released.session_id)
    monkeypatch.setattr(
        room.tools,
        "fetch_room_context",
        AsyncMock(side_effect=OSError("platform down")),
    )

    with pytest.raises(OSError, match="platform down"):
        await room.send(QUESTION)

    assert room.claude.resumed == [None, released.session_id]
    assert room.failures == [GENERIC_PROVIDER_FAILURE_MESSAGE]
    monkeypatch.undo()
    room.tools.set_room_context(room_transcript(question_id="msg-3"))
    room.claude.script([room.model_reply("Teal.")])
    await room.send(QUESTION)
    assert EARLIER_FACT in room.claude.prompts[-1]


async def test_a_fallback_turn_lost_to_a_dead_cli_replays_history_on_retry(
    claude_room: OpenRoom, tmp_path: Path
) -> None:
    room = await released_room(claude_room, tmp_path)
    [released] = room.claude.sessions
    room.claude.unresumable.add(released.session_id)
    room.tools.set_room_context([transcript_item("msg-1", EARLIER_FACT)])
    room.claude.script([Hangup()], [room.model_reply("Teal.")])
    with pytest.raises(CLIConnectionError):
        await room.send(QUESTION)

    await room.send(QUESTION)

    assert MEMORY_FRAMING in room.claude.prompts[-1]
    assert EARLIER_FACT in room.claude.prompts[-1]


async def test_a_room_mid_turn_is_not_released(claude_room: OpenRoom) -> None:
    room = await claude_room()
    working = Hold()
    room.claude.script(
        [room.model_reply("Noted.")], [working, room.model_reply("Done.")]
    )
    await room.send(EARLIER_FACT)
    await room.settled()

    turn = room.send_in_background("Keep going")
    async with working:
        await room.adapter.release_room_resources(room.room_id)
        assert room.claude.sessions[0].alive
    await turn
    await room.settled()

    assert len(room.claude.sessions) == 1


async def test_leaving_after_a_release_forgets_the_session(
    claude_room: OpenRoom, tmp_path: Path
) -> None:
    room = await released_room(claude_room, tmp_path)
    await room.leave()
    room.claude.script([room.model_reply("Hello.")])

    await room.send("Hi")

    assert room.claude.resumed == [None, None]
