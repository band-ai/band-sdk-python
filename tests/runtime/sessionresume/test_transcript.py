"""A seed's transcript is the room's history before the turn, from every page,
shaped as bootstrap history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from band.core.types import MessageType
from band.runtime.sessionresume import fetch_transcript
from band.testing import FakeAgentTools
from tests.roomhistory import original, stored

START = datetime(2024, 1, 1, tzinfo=UTC)
NEXT = START + timedelta(days=1)

# Three pages at the platform's default page size of 50.
HISTORY = [
    stored(
        f"message {index}",
        message_id=f"m{index}",
        inserted_at=START + timedelta(seconds=index),
    )
    for index in range(120)
]
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
            tools,
            room_id="room-1",
            messages=(
                original("m115", START + timedelta(seconds=115)),
                original("m110", START + timedelta(seconds=110)),
            ),
        )

        assert contents(transcript) == EARLIER
        assert [call["page"] for call in tools.context_calls] == [1, 2, 3]

    async def test_turn_inputs_not_yet_stored_keep_the_whole_history(self) -> None:
        tools = FakeAgentTools(room_context=HISTORY[:3])

        transcript = await fetch_transcript(
            tools, room_id="room-1", messages=(original("new", NEXT),)
        )

        assert contents(transcript) == ["message 0", "message 1", "message 2"]

    async def test_mentions_and_senders_read_as_on_bootstrap(self) -> None:
        tools = FakeAgentTools(
            participants=[BOB],
            room_context=[
                stored(
                    f"@[[{BOB['id']}]] the milk is in the fridge", inserted_at=START
                ),
                stored(
                    "noted",
                    message_id="own",
                    sender_name="Helper",
                    sender_type="Agent",
                    inserted_at=START,
                ),
            ],
        )

        transcript = await fetch_transcript(
            tools, room_id="room-1", messages=(original("new", NEXT),)
        )

        assert transcript == [
            {
                "role": "user",
                "content": "@bob the milk is in the fridge",
                "sender_name": "Alice",
                "sender_type": "User",
                "message_type": MessageType.TEXT,
                "metadata": {},
            },
            {
                "role": "assistant",
                "content": "noted",
                "sender_name": "Helper",
                "sender_type": "Agent",
                "message_type": MessageType.TEXT,
                "metadata": {},
            },
        ]

    async def test_an_empty_room_has_an_empty_transcript(self) -> None:
        tools = FakeAgentTools()

        transcript = await fetch_transcript(
            tools, room_id="room-1", messages=(original("new", NEXT),)
        )

        assert transcript == []
        assert [call["page"] for call in tools.context_calls] == [1]

    async def test_an_omitted_earliest_input_stops_before_later_stored_batch_inputs(
        self,
    ) -> None:
        earlier = stored("earlier", inserted_at=START)
        tools = FakeAgentTools(
            room_context=[
                earlier,
                stored("pending", message_id="pending", inserted_at=NEXT),
            ]
        )

        transcript = await fetch_transcript(
            tools,
            room_id="room-1",
            messages=(
                original("omitted", START + timedelta(seconds=1)),
                original("pending", NEXT),
            ),
        )

        assert contents(transcript) == ["earlier"]

    async def test_a_missing_cutoff_timestamp_is_an_unavailable_transcript(
        self,
    ) -> None:
        tools = FakeAgentTools(
            room_context=[stored("unknown timestamp", inserted_at=None)]
        )

        with pytest.raises(ValueError, match="Cannot order room history"):
            await fetch_transcript(
                tools, room_id="room-1", messages=(original("omitted", NEXT),)
            )
