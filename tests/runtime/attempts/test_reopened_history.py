"""A newly reopened original outranks its older failed history snapshot."""

from __future__ import annotations

import pytest

from band.client.streaming import DeliveryStatus
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.runtime.sessionconfig import SessionConfig
from tests.platform.settlement.kit import ROOM_ID, link_over
from tests.runtime.attempts.kit import HistoryRoomPeer, history_statuses
from tests.runtime.recovery.kit import ScriptedAdapter, Shape, wired_harness

ORIGINAL = "m1"
WARMUP = "warmup"


@pytest.mark.looptime
class TestReopenedHistory:
    async def test_fresh_processing_overrides_cached_failed_history(self) -> None:
        peer = HistoryRoomPeer()
        adapter = ScriptedAdapter()
        peer.post(ORIGINAL)
        peer.post(WARMUP)
        async with peer.client() as rest:
            external = link_over(rest)
            assert await external.mark_processing(ROOM_ID, ORIGINAL)
            assert await external.mark_failed(
                ROOM_ID, ORIGINAL, GENERIC_PROVIDER_FAILURE_MESSAGE
            )
            room = wired_harness(
                peer,
                adapter,
                rest,
                Shape.SINGLETON,
                config=SessionConfig(
                    consume_all_queued=True,
                    enable_context_hydration=True,
                    enable_working_state=False,
                ),
            )
            try:
                await room.runtime.rooms.admit(ROOM_ID)
                await room.drain()
                assert adapter.turns == [(WARMUP,)]
                assert (await history_statuses(room.ctx))[
                    ORIGINAL
                ] == DeliveryStatus.FAILED

                assert await external.mark_processing(ROOM_ID, ORIGINAL)
                assert peer.statuses[ORIGINAL] is DeliveryStatus.PROCESSING
                await room.ctx.request_resync()
                await room.drain()

                assert adapter.turns == [(WARMUP,), (ORIGINAL,)]
                assert peer.statuses[ORIGINAL] is DeliveryStatus.PROCESSED
                assert (await history_statuses(room.ctx))[
                    ORIGINAL
                ] == DeliveryStatus.FAILED
                assert peer.context_requests == 1
            finally:
                await room.runtime.stop()
