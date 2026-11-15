"""A turn of one original still names its sender and shows its text once."""

from __future__ import annotations

import pytest

from tests.framework_conformance.batchprobes import SINGLETON, current_records
from tests.framework_conformance.batchprobes.registry import (
    BATCH_PROBES,
    provider_received,
)


class TestBatchSingleton:
    @pytest.mark.parametrize("framework_id", sorted(BATCH_PROBES))
    async def test_the_one_sender_is_identified(self, framework_id: str) -> None:
        provider_input = await provider_received(framework_id, SINGLETON)

        assert current_records(provider_input) == [o.record for o in SINGLETON]

    @pytest.mark.parametrize("framework_id", sorted(BATCH_PROBES))
    async def test_its_text_is_shown_once(self, framework_id: str) -> None:
        provider_input = await provider_received(framework_id, SINGLETON)

        assert provider_input.count(SINGLETON[0].content) == 1
