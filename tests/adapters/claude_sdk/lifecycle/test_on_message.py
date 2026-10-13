from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.core.exceptions import InputNotAccepted
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import ClaudeRoom, said
from tests.conftest import eager_tasks, requires_eager_tasks

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]

MEMORY_FRAMING = "Your memory of this room so far"


async def test_a_room_bootstraps_once_then_keeps_talking_in_the_same_session(
    claude_room: OpenRoom,
) -> None:
    """The first message carries the room's history, framed as the agent's
    own memory; later messages reuse the session and never repeat it."""
    room = await claude_room()
    room.claude.script([room.model_reply("hi Bob")], [room.model_reply("still here")])

    await room.send("Hello, agent!", history=(said("Alice", "the code word is tulip"),))
    await room.send("Are you there?")

    first, second = room.claude.prompts
    assert MEMORY_FRAMING in first
    assert "[Alice]: the code word is tulip" in first
    assert "chat_id" not in first
    assert "Hello, agent!" in first
    assert MEMORY_FRAMING not in second
    assert "Are you there?" in second
    assert len(room.claude.sessions) == 1
    assert room.chat == ["hi Bob", "still here"]
    assert room.failures == []


@requires_eager_tasks
async def test_a_turn_runs_under_an_eager_task_factory(claude_room: OpenRoom) -> None:
    """The worker can start inside ``create_task`` before the adapter records
    it, and still runs under its own owner."""
    room = await claude_room()
    room.claude.script([room.model_reply("Hello.")])

    with eager_tasks():
        await room.send("hi")

    assert room.chat == ["Hello."]
    assert room.failures == []


async def test_a_second_message_is_refused_while_the_first_is_still_running(
    claude_room: OpenRoom,
) -> None:
    """Unaccepted work reaches no model and the active turn finishes normally."""
    room = await claude_room()
    thinking = Hold()
    room.claude.script([thinking, room.model_reply("done")])

    first = asyncio.create_task(room.send("do the long thing"))
    async with thinking:
        with pytest.raises(InputNotAccepted):
            await room.send("are you done yet?")
    await first

    assert room.chat == ["done"]
    assert len(room.claude.prompts) == 1
