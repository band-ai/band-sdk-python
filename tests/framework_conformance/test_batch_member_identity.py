"""Every original of a batch reaches the model once, as its own sender's input."""

from __future__ import annotations

import pytest

from tests.framework_conformance.batchprobes import (
    BATCH,
    FINAL_TEXT,
    current_records,
    delivery_input,
)
from tests.framework_conformance.batchprobes.registry import BATCH_PROBES


class TestBatchMemberIdentity:
    @pytest.mark.parametrize("framework_id", sorted(BATCH_PROBES))
    async def test_each_original_is_shown_once_in_order_with_its_own_sender(
        self, framework_id: str
    ) -> None:
        provider_input = await BATCH_PROBES[framework_id].run(
            await delivery_input(BATCH), FINAL_TEXT
        )

        assert current_records(provider_input) == [o.record for o in BATCH]

    @pytest.mark.parametrize("framework_id", sorted(BATCH_PROBES))
    async def test_no_original_text_is_repeated_elsewhere_in_the_input(
        self, framework_id: str
    ) -> None:
        provider_input = await BATCH_PROBES[framework_id].run(
            await delivery_input(BATCH), FINAL_TEXT
        )

        assert [provider_input.count(o.content) for o in BATCH] == [1, 1, 1]
