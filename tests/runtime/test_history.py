"""A refetched transcript hands a fresh session only what preceded its turn."""

from __future__ import annotations

from typing import Any

from band.runtime.history import fetch_earlier_messages
from band.testing import FakeAgentTools

ROOM = "room-1"


def transcript_item(msg_id: str) -> dict[str, Any]:
    return {
        "id": msg_id,
        "content": f"said {msg_id}",
        "sender_id": "user-1",
        "sender_type": "User",
        "message_type": "text",
    }


async def earlier_ids(transcript: list[str], *, trigger_id: str) -> list[str]:
    tools = FakeAgentTools()
    tools.set_room_context([transcript_item(msg_id) for msg_id in transcript])
    earlier = await fetch_earlier_messages(tools, room_id=ROOM, trigger_id=trigger_id)
    return [message["id"] for message in earlier]


async def test_the_trigger_and_later_pending_turns_are_left_out() -> None:
    assert await earlier_ids(["m1", "m2", "trigger", "m4"], trigger_id="trigger") == [
        "m1",
        "m2",
    ]


async def test_a_trigger_missing_from_the_transcript_keeps_everything() -> None:
    assert await earlier_ids(["m1", "m2"], trigger_id="trigger") == ["m1", "m2"]
