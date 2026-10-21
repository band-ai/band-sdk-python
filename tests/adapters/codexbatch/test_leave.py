"""Leaving a room ends a batch turn through the room's retained-room ownership."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import InputNotAccepted
from band.testing import failure_reports
from tests.adapters.codex.kit import CountingCloseClient
from tests.adapters.codexbatch.kit import (
    ROOM_ID,
    CodexBatchRoom,
    OpenRoom,
    approval_request,
    reply_to_room,
)
from tests.adapters.codexturns import make_codex_adapter, turn_completed


class TestBatchLeave:
    async def test_leaving_declines_the_approval_a_batch_is_parked_on(
        self, codex_room: OpenRoom
    ) -> None:
        room = await codex_room(approval_request(10), turn_completed())
        await room.send_batch(*room.alice_helper_alice())
        parked = room.turn

        await room.adapter.on_cleanup(ROOM_ID)
        await parked

        assert (
            room.decisions,
            room.pending_approval_count,
            room.client.closed,
        ) == ([(10, "decline")], 0, True)

    async def test_a_batch_meeting_a_leaving_room_is_refused_not_failed(self) -> None:
        client = CountingCloseClient(
            events=[reply_to_room(), turn_completed()], hold=True
        )
        adapter = make_codex_adapter(client)
        await adapter.on_started("Agent", "A coding agent")
        room = CodexBatchRoom(adapter, client)
        await room.send("hello")
        leaving = asyncio.create_task(adapter.on_cleanup(ROOM_ID))
        await client.closing.wait()

        with pytest.raises(InputNotAccepted):
            await room.send_batch(*room.alice_helper_alice())
        refused = room.deliveries[-1]
        client.finish_close.set()
        await leaving

        assert (failure_reports(refused), len(room.turn_inputs)) == ([], 1)
