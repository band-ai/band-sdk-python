"""A resync requested while a recovery pass runs is served by another pass."""

from __future__ import annotations

import pytest

from band.platform.event import ReconnectedEvent
from tests.runtime.batching.kit import FakeRoom, started_room

pytestmark = pytest.mark.looptime


class TestResyncDuringPass:
    async def test_a_reconnect_while_the_startup_pass_reads_triggers_another_pass(
        self, platform: FakeRoom
    ) -> None:
        platform.during_read.append(ReconnectedEvent())

        async with started_room(platform) as room:
            await room.quiesce()

        assert platform.page_reads == 2
