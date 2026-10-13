"""Room workspace lifecycle: one room starting or closing never blocks another room."""

from __future__ import annotations

import asyncio

from tests.adapters.hold import Hold
from tests.adapters.lifecycle.kit import RetentionHost
from tests.adapters.roompeer import PeerCommand, write_instruction


class TestRoomIndependence:
    """One room starting or closing never blocks another room."""

    async def test_rooms_progress_while_another_room_starts_or_closes(
        self,
        host: RetentionHost,
    ) -> None:
        host.shared = False
        starting = host.next_start = Hold()
        opening = asyncio.create_task(host.send("room-a", write_instruction("alpha")))
        async with starting:
            assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
        assert await opening == ["alpha"]

        closing = host.children[0].closing = Hold()
        leaving = asyncio.create_task(host.leave("room-a"))
        async with closing:
            assert await host.send("room-b", PeerCommand.READ) == ["Read: bravo"]
        await leaving
        assert host.exited == [True, False]
