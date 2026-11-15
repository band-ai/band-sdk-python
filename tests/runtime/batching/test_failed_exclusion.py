"""Batch mode never replays a durably failed input; unfinished work stays recoverable."""

from __future__ import annotations

from band.client.streaming import DeliveryStatus
from tests.runtime.batching.kit import FakeRoom, started_room
from tests.runtime.helpers import runtime_failures


class TestFailedExclusion:
    async def test_a_failed_input_is_skipped_and_a_processing_one_is_taken_up(
        self, platform: FakeRoom
    ) -> None:
        platform.post("failed", status=DeliveryStatus.FAILED)
        platform.post("interrupted", status=DeliveryStatus.PROCESSING)
        platform.post("fresh")

        async with started_room(platform) as room:
            await room.settled("interrupted", "fresh")

        assert (room.turns.ids, platform.status_of("failed")) == (
            [("interrupted", "fresh")],
            DeliveryStatus.FAILED,
        )

    async def test_a_failed_batch_fails_every_member_once_and_is_not_replayed(
        self, platform: FakeRoom
    ) -> None:
        for message_id in ("m1", "m2", "m3"):
            platform.post(message_id)

        async with started_room(platform, fail_on=frozenset({"m2"})) as room:
            await room.reached(DeliveryStatus.FAILED, "m1", "m2", "m3")
            await room.resync()

        assert room.turns.ids == [("m1", "m2", "m3")]
        assert len(runtime_failures(room.ctx.link)) == 1
