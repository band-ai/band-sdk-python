"""A delivery the REST list omits still runs, ordered by its own position."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.runtime.batching.kit import FakeRoom, started_room


class TestWebSocketOnlyOriginal:
    @pytest.mark.asyncio
    async def test_it_runs_alone_between_the_rest_work_around_it(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        platform.post("m1", at=1)
        platform.post("m3", at=3)
        reply = platform.post("reply", at=2, in_rest=False)

        async with started_room(platform, mock_rest_client, arrived=(reply,)) as room:
            await room.settled("m1", "reply", "m3")

        assert room.turns.ids == [("m1",), ("reply",), ("m3",)]

    @pytest.mark.asyncio
    async def test_a_refused_admission_keeps_its_payload_for_the_next_pass(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        platform.refuse_processing.add("reply")

        async with started_room(platform, mock_rest_client) as room:
            await room.deliver(platform.post("reply", in_rest=False))
            await room.until(lambda: ("processing", "reply") in platform.marks)
            platform.refuse_processing.clear()
            await room.ctx.request_resync()
            await room.settled("reply")

        assert room.turns.ids == [("reply",)]
