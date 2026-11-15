"""The started-turn event summarizes every original of the turn, not only the first."""

from __future__ import annotations

from band.testing import FakeAgentTools
from tests.adapters.codexbatch.kit import OpenRoom, reply_to_room
from tests.adapters.codexturns import turn_completed


def input_summaries(tools: FakeAgentTools) -> list[str]:
    return [
        event["metadata"]["codex_input_summary"]
        for event in tools.events_sent
        if event["metadata"].get("codex_event_type") == "turn_lifecycle"
        and event["metadata"].get("codex_turn_status") == "started"
    ]


class TestInputSummary:
    async def test_a_batch_is_summarized_by_all_its_originals(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(
            reply_to_room(), turn_completed(), emit_turn_lifecycle_events=True
        )
        originals = room.alice_helper_alice()

        tools = await room.send_batch(*originals)

        assert input_summaries(tools) == ["\n".join(m.content for m in originals)]

    async def test_a_single_original_is_summarized_by_its_content(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(
            reply_to_room(), turn_completed(), emit_turn_lifecycle_events=True
        )

        await room.send("fix the login bug")

        assert input_summaries(room.deliveries[0]) == ["fix the login bug"]
