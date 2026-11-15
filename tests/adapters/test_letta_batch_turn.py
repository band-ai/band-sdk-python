"""A batch turn reaches the Letta agent as one message holding every original."""

from __future__ import annotations

from typing import Any

from tests.adapters.lettakit import ready_letta_adapter, scripted_letta_turn
from tests.framework_conformance.batchprobes import (
    BATCH,
    FINAL_TEXT,
    current_records,
    deliver,
    delivery_input,
)
from tests.runtime.helpers import ROOM_ID


class TestLettaBatchTurn:
    async def test_a_warm_batch_is_one_message_with_each_original_in_order(
        self,
    ) -> None:
        adapter, client = ready_letta_adapter(room_id=ROOM_ID)
        scripted = scripted_letta_turn(adapter, room_id=ROOM_ID, final_text=FINAL_TEXT)
        # Per call to the agent, the current-input records of each message sent.
        calls: list[list[list[dict[str, Any]]]] = []

        async def create(**request: Any) -> Any:
            calls.append([current_records(m["content"]) for m in request["messages"]])
            return await scripted(**request)

        client.agents.messages.create.side_effect = create

        await deliver(adapter, await delivery_input(BATCH))

        assert calls == [[[original.record for original in BATCH]]]
