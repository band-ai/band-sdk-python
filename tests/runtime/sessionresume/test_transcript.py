"""A seed's transcript is the room's history before the turn, from every page,
shaped as bootstrap history."""

from __future__ import annotations

from typing import Any

import pytest

from band.runtime.sessionresume import fetch_transcript
from band.testing import FakeAgentTools
from tests.roomhistory import stored

# Three pages at the platform's default page size of 50.
HISTORY = [stored(f"message {index}", message_id=f"m{index}") for index in range(120)]
EARLIER = [f"message {index}" for index in range(110)]
BOB = {
    "id": "u1",
    "name": "Bob",
    "handle": "bob",
    "type": "User",
    "role": "member",
    "status": "active",
}


def contents(messages: list[dict[str, Any]]) -> list[str]:
    return [message["content"] for message in messages]


class TestFetchTranscript:
    @pytest.mark.parametrize(
        "reports_page_count", [True, False], ids=["page-count", "short-page"]
    )
    async def test_every_page_up_to_the_turns_earliest_input_is_kept(
        self, reports_page_count: bool
    ) -> None:
        """The turn's inputs and everything after them are turns of their own.
        Without a page count, a short page ends the history."""
        tools = FakeAgentTools(room_context=HISTORY)
        tools.reports_context_page_count = reports_page_count

        transcript = await fetch_transcript(
            tools, room_id="room-1", turn_ids={"m115", "m110"}
        )

        assert contents(transcript) == EARLIER
        assert [call["page"] for call in tools.context_calls] == [1, 2, 3]

    async def test_turn_inputs_not_yet_stored_keep_the_whole_history(self) -> None:
        tools = FakeAgentTools(room_context=HISTORY[:3])

        transcript = await fetch_transcript(tools, room_id="room-1", turn_ids={"new"})

        assert contents(transcript) == ["message 0", "message 1", "message 2"]

    async def test_mentions_and_senders_read_as_on_bootstrap(self) -> None:
        tools = FakeAgentTools(
            participants=[BOB],
            room_context=[
                stored("@[[u1]] the milk is in the fridge"),
                stored("noted", message_id="own", sender_type="Agent"),
            ],
        )

        mention, reply = await fetch_transcript(
            tools, room_id="room-1", turn_ids={"new"}
        )

        assert mention["content"] == "@bob the milk is in the fridge"
        assert (mention["role"], reply["role"]) == ("user", "assistant")

    async def test_an_empty_room_has_an_empty_transcript(self) -> None:
        tools = FakeAgentTools()

        transcript = await fetch_transcript(tools, room_id="room-1", turn_ids={"new"})

        assert transcript == []
        assert [call["page"] for call in tools.context_calls] == [1]
