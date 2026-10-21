"""A NOOA room agent remembers the originals of an earlier batch turn."""

from __future__ import annotations

from tests.framework_conformance.batchprobes import BATCH, current_records
from tests.framework_conformance.batchprobes.laterturn import (
    NEXT_INPUT,
    deliver_batch_then_next_turn,
)
from tests.framework_conformance.batchprobes.nooa import (
    model_input,
    recording_adapter,
)


class TestBatchMemory:
    async def test_a_later_turn_shows_each_original_once_in_order_before_its_own(
        self,
    ) -> None:
        adapter, llm = await recording_adapter(turns=2)

        await deliver_batch_then_next_turn(adapter)

        assert current_records(model_input(llm)) == [
            o.record for o in (*BATCH, NEXT_INPUT)
        ]
