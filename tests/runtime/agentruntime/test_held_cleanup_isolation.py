"""One room's held cleanup never blocks another room's events."""

from __future__ import annotations

import asyncio

from band.runtime.runtime import AgentRuntime
from tests.conftest import (
    make_message_event,
    make_room_added_event,
    make_room_removed_event,
)
from tests.runtime.agentruntime.kit import QueueLink
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import RoomCleanup


class TestHeldCleanupIsolation:
    """One room's held cleanup never blocks another room's events."""

    async def test_held_room_cleanup_does_not_block_other_rooms_events(
        self, mock_link, mock_handler, room_cleanup: RoomCleanup
    ):
        feed = QueueLink(mock_link)
        handled = asyncio.Event()
        mock_handler.side_effect = lambda ctx, event: handled.set()
        runtime = AgentRuntime(
            mock_link, "agent-123", mock_handler, on_session_cleanup=room_cleanup
        )
        await runtime.start()
        try:
            feed.events.put_nowait(make_room_added_event("room-1"))
            feed.events.put_nowait(make_room_added_event("room-2"))
            await wait_for_condition(lambda: "room-2" in runtime.executions)

            room_cleanup.hold()
            feed.events.put_nowait(make_room_removed_event("room-1"))
            await room_cleanup.entered.wait()
            feed.events.put_nowait(make_message_event(room_id="room-2", msg_id="m-2"))
            await asyncio.wait_for(handled.wait(), timeout=1)
            assert room_cleanup.rooms == []
            assert "room-1" not in runtime.executions
        finally:
            room_cleanup.release.set()
            await runtime.stop()
