"""The Claude CLI can fire one band_send_message call more than once under
load; the room must still see one message."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from tests.adapters.claude_sdk.helpers import ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def test_a_repeated_reply_reaches_its_room_once_even_after_the_turn_ends(
    claude_room: OpenRoom,
) -> None:
    """A duplicate inside the turn and one lingering into the next turn --
    delivered with the fresh tools the runtime builds per message -- both
    collapse, while a new reply and another room's identical reply go out."""
    room = await claude_room()
    other_room = room.beside("room-2")
    next_turn_tools = room.fresh_tools()
    room.claude.script(
        [room.model_reply("hello"), room.model_reply("hello")],
        [room.model_reply("hello"), room.model_reply("a new answer")],
        [other_room.model_reply("hello")],
    )

    await room.send("say hello")
    await room.send("anything else?", tools=next_turn_tools)
    await other_room.send("say hello")

    assert room.chat == ["hello"]
    assert [message["content"] for message in next_turn_tools.messages_sent] == [
        "a new answer"
    ]
    assert other_room.chat == ["hello"]
    assert room.failures == []


async def test_leaving_a_room_forgets_what_it_already_said(
    claude_room: OpenRoom,
) -> None:
    room = await claude_room()
    room.claude.script([room.model_reply("hello")], [room.model_reply("hello")])

    await room.send("say hello")
    await room.leave()
    await room.send("say hello again")

    assert room.chat == ["hello", "hello"]


async def test_a_zero_ttl_turns_the_dedup_off(claude_room: OpenRoom) -> None:
    room = await claude_room(send_message_dedup_ttl_seconds=0)
    room.claude.script([room.model_reply("hello"), room.model_reply("hello")])

    await room.send("say hello")

    assert room.chat == ["hello", "hello"]


def test_a_negative_ttl_is_rejected() -> None:
    with pytest.raises(ValueError):
        ClaudeSDKAdapter(send_message_dedup_ttl_seconds=-1)
