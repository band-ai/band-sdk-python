"""Bootstrap history ends before the turn's earliest original and repeats none."""

from __future__ import annotations

from typing import Any

import pytest

from band.preprocessing.default import DefaultPreprocessor
from tests.conftest import make_message_event
from tests.runtime.batching.kit import room_context

AGENT_ID = "agent-1"


def stored(message_id: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "sender_type": "User",
        "sender_name": "Alice",
        "content": message_id,
    }


async def history_contents(*, stored_ids: list[str]) -> list[str]:
    ctx = room_context(stored=[stored(i) for i in stored_ids])
    events = [make_message_event(msg_id=i) for i in ("m1", "m2")]
    inp = await DefaultPreprocessor().process_batch(ctx, events, AGENT_ID)
    assert inp is not None
    return [message["content"] for message in inp.history.raw]


class TestBatchHistory:
    @pytest.mark.asyncio
    async def test_history_stops_before_the_earliest_original(self) -> None:
        stored_ids = ["h1", "h2", "m1", "m2", "m3"]

        assert await history_contents(stored_ids=stored_ids) == ["h1", "h2"]

    @pytest.mark.asyncio
    async def test_a_later_original_is_not_repeated_when_the_first_is_not_yet_stored(
        self,
    ) -> None:
        assert await history_contents(stored_ids=["h1", "m2"]) == ["h1"]
