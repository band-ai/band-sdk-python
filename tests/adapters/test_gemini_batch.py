"""A Gemini room's later turns carry what its earlier batch delivered."""

from __future__ import annotations

import pytest

from tests.framework_conformance.batchprobes import BATCH, FINAL_TEXT, current_records
from tests.framework_conformance.batchprobes.gemini import recording_adapter
from tests.framework_conformance.batchprobes.laterturn import (
    NEXT_INPUT,
    deliver_batch_then_next_turn,
)

pytest.importorskip("google.genai", reason="google-genai not installed")


class TestGeminiBatchHistory:
    async def test_a_later_turn_carries_each_original_once_in_order_before_its_own(
        self,
    ) -> None:
        adapter, requests = await recording_adapter(FINAL_TEXT)

        await deliver_batch_then_next_turn(adapter)

        assert current_records(requests[-1]) == [o.record for o in (*BATCH, NEXT_INPUT)]
