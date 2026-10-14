"""A turn of one original still names its sender and shows its text once."""

from __future__ import annotations

import pytest

from tests.framework_conformance.batchprobes import (
    FINAL_TEXT,
    SINGLETON,
    current_records,
    delivery_input,
)
from tests.framework_conformance.batchprobes.registry import BATCH_PROBES


class TestBatchSingleton:
    @pytest.mark.parametrize("framework_id", sorted(BATCH_PROBES))
    async def test_the_one_sender_is_identified(self, framework_id: str) -> None:
        provider_input = await BATCH_PROBES[framework_id].run(
            await delivery_input(SINGLETON), FINAL_TEXT
        )

        assert current_records(provider_input) == [o.record for o in SINGLETON]

    @pytest.mark.parametrize("framework_id", sorted(BATCH_PROBES))
    async def test_its_text_is_shown_once(self, framework_id: str) -> None:
        provider_input = await BATCH_PROBES[framework_id].run(
            await delivery_input(SINGLETON), FINAL_TEXT
        )

        assert provider_input.count(SINGLETON[0].content) == 1
