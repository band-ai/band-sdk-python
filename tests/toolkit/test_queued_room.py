"""Queued replies survive a delayed observer frame for the opener."""

from __future__ import annotations

from tests.toolkit.queuedroom import queued_room


class TestQueuedRoomReplies:
    async def test_a_queued_reply_is_retained_when_the_opener_status_arrives_late(
        self,
    ) -> None:
        async with queued_room() as room:
            batch = await room.queue_texts_while_busy(["queued-answer"])

        batch.replies.assert_contains_any(["queued-answer"])
        assert room.partition == batch.batched_turns
        assert batch.processed == batch.originals
