"""An adapter's own relay addresses each distinct sender of the turn once."""

from __future__ import annotations

from typing import Any

import pytest

from tests.framework_conformance.batchprobes import (
    ALICE_FIRST,
    BATCH,
    FINAL_TEXT,
    HELPER,
    SINGLETON,
    delivery_input,
    posted_messages,
)
from tests.framework_conformance.batchprobes.registry import BATCH_PROBES

RELAYING = sorted(name for name, probe in BATCH_PROBES.items() if probe.relays)
SILENT = sorted(name for name, probe in BATCH_PROBES.items() if not probe.relays)


def mentioned_ids(post: dict[str, Any]) -> list[str]:
    return [m if isinstance(m, str) else m["id"] for m in post["mentions"]]


class TestBatchAudience:
    @pytest.mark.parametrize("framework_id", SILENT)
    async def test_an_adapter_without_a_relay_addresses_nobody_itself(
        self, framework_id: str
    ) -> None:
        inp = await delivery_input(BATCH)

        await BATCH_PROBES[framework_id].run(inp, FINAL_TEXT)

        assert posted_messages(inp) == []

    @pytest.mark.parametrize("framework_id", RELAYING)
    async def test_a_relay_mentions_each_distinct_sender_once(
        self, framework_id: str
    ) -> None:
        inp = await delivery_input(BATCH)

        await BATCH_PROBES[framework_id].run(inp, FINAL_TEXT)

        assert [mentioned_ids(post) for post in posted_messages(inp)] == [
            [ALICE_FIRST.sender_id, HELPER.sender_id]
        ]

    @pytest.mark.parametrize("framework_id", RELAYING)
    async def test_a_relay_of_a_singleton_mentions_its_one_sender(
        self, framework_id: str
    ) -> None:
        inp = await delivery_input(SINGLETON)

        await BATCH_PROBES[framework_id].run(inp, FINAL_TEXT)

        assert [mentioned_ids(post) for post in posted_messages(inp)] == [
            [ALICE_FIRST.sender_id]
        ]
