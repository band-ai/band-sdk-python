"""Batch mode never replays a durably failed input; unfinished work stays recoverable."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from band.client.streaming import DeliveryStatus
from tests.runtime.batching.kit import FakeRoom, started_room


class TestFailedExclusion:
    @pytest.mark.asyncio
    async def test_a_failed_input_is_skipped_and_a_processing_one_is_taken_up(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        platform.post("failed", status=DeliveryStatus.FAILED)
        platform.post("interrupted", status=DeliveryStatus.PROCESSING)
        platform.post("fresh")

        async with started_room(platform, mock_rest_client) as room:
            await room.settled("interrupted", "fresh")

        assert (room.turns.ids, platform.status_of("failed")) == (
            [("interrupted", "fresh")],
            DeliveryStatus.FAILED,
        )

    @pytest.mark.asyncio
    async def test_a_failed_batch_fails_every_member_once_and_is_not_replayed(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        for message_id in ("m1", "m2", "m3"):
            platform.post(message_id)

        async with started_room(
            platform, mock_rest_client, fail_on=frozenset({"m2"})
        ) as room:
            await room.until(
                lambda: all(
                    platform.status_of(m) == DeliveryStatus.FAILED
                    for m in ("m1", "m2", "m3")
                )
            )
            reads = room.ctx.link.get_actionable_page.await_count
            await room.ctx.request_resync()
            await room.until(
                lambda: room.ctx.link.get_actionable_page.await_count > reads
            )

        assert (
            room.turns.ids,
            mock_rest_client.agent_api_events.create_agent_chat_event.await_count,
        ) == (
            [("m1", "m2", "m3")],
            1,
        )
