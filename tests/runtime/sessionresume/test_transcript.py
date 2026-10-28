"""A seed's transcript is the room's history before the turn, from every page."""

from __future__ import annotations

from typing import Any

import pytest

from band.runtime.sessionresume import fetch_transcript
from band.testing import FakeAgentTools
from tests.roomhistory import stored

# Three pages at the platform's default page size of 50.
HISTORY = [stored(f"message {index}", message_id=f"m{index}") for index in range(120)]
EARLIER_IDS = [f"m{index}" for index in range(110)]


def ids(messages: list[dict[str, Any]]) -> list[str]:
    return [message["id"] for message in messages]


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

        assert ids(transcript) == EARLIER_IDS
        assert [call["page"] for call in tools.context_calls] == [1, 2, 3]

    async def test_turn_inputs_not_yet_stored_keep_the_whole_history(self) -> None:
        tools = FakeAgentTools(room_context=HISTORY[:3])

        transcript = await fetch_transcript(tools, room_id="room-1", turn_ids={"new"})

        assert ids(transcript) == ["m0", "m1", "m2"]

    async def test_an_empty_room_has_an_empty_transcript(self) -> None:
        tools = FakeAgentTools()

        transcript = await fetch_transcript(tools, room_id="room-1", turn_ids={"new"})

        assert transcript == []
        assert [call["page"] for call in tools.context_calls] == [1]
