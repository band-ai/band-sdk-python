from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]

MEMORY_FRAMING = "Your memory of this room so far"


async def test_a_room_bootstraps_once_then_keeps_talking_in_the_same_session(
    claude_room: OpenRoom,
) -> None:
    """The first message carries the room's history, framed as the agent's
    own memory; later messages reuse the session and never repeat it."""
    room = await claude_room()
    room.claude.script([room.model_reply("hi Bob")], [room.model_reply("still here")])

    await room.send("Hello, agent!", history="[Alice]: the code word is tulip")
    await room.send("Are you there?")

    first, second = room.claude.prompts
    assert MEMORY_FRAMING in first
    assert "[Alice]: the code word is tulip" in first
    assert f"[chat_id: {room.room_id}]" in first
    assert "Hello, agent!" in first
    assert MEMORY_FRAMING not in second
    assert "Are you there?" in second
    assert len(room.claude.sessions) == 1
    assert room.chat == ["hi Bob", "still here"]
    assert room.failures == []


async def test_a_second_message_is_refused_while_the_first_is_still_running(
    claude_room: OpenRoom,
) -> None:
    """One room runs one turn: a message arriving mid-turn is told to wait,
    reaches no model, and the running turn still finishes normally."""
    room = await claude_room()
    thinking = Hold()
    room.claude.script([thinking, room.model_reply("done")])

    first = asyncio.create_task(room.send("do the long thing"))
    async with thinking:
        await room.send("are you done yet?")
    await first

    assert room.chat == ["Still processing the previous request in this room.", "done"]
    assert room.tools.messages_sent[0]["mentions"] == ["u1"]
    assert len(room.claude.prompts) == 1
