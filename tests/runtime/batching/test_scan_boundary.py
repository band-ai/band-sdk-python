"""A boundary event does not move the scan past REST rows older than the page it skipped."""

from __future__ import annotations

import asyncio

import pytest

from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_participant_added_event
from tests.runtime.batching.kit import FakeRoom, started_room
from tests.runtime.helpers import AGENT_ID, ROOM_ID

pytestmark = pytest.mark.looptime

PAGE_SIZE = 2
REST_ROWS = [f"row-{index}" for index in range(1, 2 * PAGE_SIZE + 2)]
# Long enough for a room that skipped rows to come back for them on its own.
PERIODIC_WAKE = SessionConfig().idle_resync_seconds + 1


class TestScanBoundary:
    async def test_rest_rows_beyond_a_skipped_local_segment_run_oldest_first(
        self, platform: FakeRoom
    ) -> None:
        platform.page_size = PAGE_SIZE
        echo = platform.post(
            "echo", sender_id=AGENT_ID, sender_type="Agent", in_rest=False
        )
        for row in REST_ROWS:
            platform.post(row)
        boundary = make_participant_added_event(room_id=ROOM_ID)

        async with started_room(platform, arrived=(echo, boundary)) as room:
            await asyncio.sleep(PERIODIC_WAKE)

        assert room.turns.order == ["participant_added", *REST_ROWS]
