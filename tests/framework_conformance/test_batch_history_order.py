"""History reaches the model before the turn's inputs, and never repeats them."""

from __future__ import annotations

import pytest

from band.runtime.formatters import CURRENT_INPUTS_HEADING
from tests.framework_conformance.batchprobes import (
    BATCH,
    EARLIER_ANSWER,
    EARLIER_QUESTION,
    LATER_PENDING,
)
from tests.framework_conformance.batchprobes.registry import (
    BATCH_PROBES,
    provider_received,
)


class TestBatchHistoryOrder:
    @pytest.mark.parametrize("framework_id", sorted(BATCH_PROBES))
    async def test_earlier_conversation_precedes_the_current_inputs(
        self, framework_id: str
    ) -> None:
        provider_input = await provider_received(framework_id, BATCH)

        current_at = provider_input.index(CURRENT_INPUTS_HEADING)
        assert provider_input.index(EARLIER_QUESTION) < current_at
        assert provider_input.index(EARLIER_ANSWER) < current_at

    @pytest.mark.parametrize("framework_id", sorted(BATCH_PROBES))
    async def test_history_ends_before_the_earliest_input_and_is_shown_once(
        self, framework_id: str
    ) -> None:
        provider_input = await provider_received(framework_id, BATCH)

        assert [
            provider_input.count(text)
            for text in (EARLIER_QUESTION, EARLIER_ANSWER, LATER_PENDING)
        ] == [1, 1, 0]
