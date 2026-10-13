"""One original delivered by REST and WebSocket runs once."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.runtime.batching.kit import FakeRoom, started_room


class TestDuplicateDelivery:
    @pytest.mark.asyncio
    async def test_a_message_from_both_sources_is_one_input(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        event = platform.post("m1")

        async with started_room(
            platform, mock_rest_client, arrived=(event, event)
        ) as room:
            await room.settled("m1")
            await room.deliver(event)
            await room.ctx.request_resync()

        assert room.turns.ids == [("m1",)]
