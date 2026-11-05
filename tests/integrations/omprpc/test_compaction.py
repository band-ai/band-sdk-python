"""Context rollover preserves prompt delivery and the next turn's connection."""

from __future__ import annotations

import asyncio

from band.integrations.ompwire import OmpEventType, OmpState
from tests.integrations.omppeers.base import COMPLETE, ROLLOVER_SUMMARY
from tests.integrations.omprpc.events import (
    EventRecorder,
    follow_up_texts,
    record_prompt,
)
from tests.integrations.omprpc.spawn import ClientFactory
from tests.integrations.ompscenarios import PeerScenario


class TestCompactionResults:
    async def test_rollover_delivers_the_reply_and_keeps_the_connection_usable(
        self, client_factory: ClientFactory
    ) -> None:
        client = await client_factory(PeerScenario.COMPACTION_RESULT)
        observer = EventRecorder()

        async with asyncio.timeout(5):
            await record_prompt(client, observer)
            compaction = await observer.wait_for(OmpEventType.AUTO_COMPACTION_END)
            assert compaction.result is not None
            assert compaction.result.model_dump() == {
                "summary": ROLLOVER_SUMMARY,
                "firstKeptEntryId": "kept-entry",
                "tokensBefore": 120000,
                "shortSummary": None,
                "details": {"kind": "experimental-context-rollover", "version": 1},
            }
            assert observer.assistant_texts == [COMPLETE]
            assert await follow_up_texts(client) == [COMPLETE]
            state = OmpState.model_validate(await client.request("get_state"))
            assert state.isSettled is True
