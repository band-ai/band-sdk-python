"""A seed's transcript is the room's history before the turn, from every page."""

from __future__ import annotations

from typing import Any

from band.runtime.sessionresume import fetch_transcript
from band.testing import FakeAgentTools
from tests.runtime.sessionresume.kit import stored

# Three pages at the platform's default page size of 50.
HISTORY = [stored(f"m{index}") for index in range(120)]
EARLIER_IDS = [f"m{index}" for index in range(110)]


def ids(messages: list[dict[str, Any]]) -> list[str]:
    return [message["id"] for message in messages]


class TestFetchTranscript:
    async def test_every_page_up_to_the_turns_earliest_input_is_kept(self) -> None:
        """The turn's inputs and everything after them are turns of their own."""
        tools = FakeAgentTools(room_context=HISTORY)

        transcript = await fetch_transcript(
            tools, room_id="room-1", turn_ids={"m115", "m110"}
        )

        assert ids(transcript) == EARLIER_IDS
        assert [call["page"] for call in tools.context_calls] == [1, 2, 3]

    async def test_a_short_page_ends_history_without_a_page_count(self) -> None:
        tools = FakeAgentTools(room_context=HISTORY)
        serve_page = tools.fetch_room_context

        async def without_page_count(**request: Any) -> dict[str, Any]:
            context = await serve_page(**request)
            del context["meta"]["total_pages"]
            return context

        tools.fetch_room_context = without_page_count

        transcript = await fetch_transcript(tools, room_id="room-1", turn_ids={"m110"})

        assert ids(transcript) == EARLIER_IDS
        assert [call["page"] for call in tools.context_calls] == [1, 2, 3]
