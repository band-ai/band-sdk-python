"""An attempt that settles nothing is not repeated in a spin."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.runtime.execution import BacklogProcessResult, ExecutionContext
from tests.runtime.batching.kit import FakeRoom, started_room


class TestUnproductiveAttempt:
    @pytest.mark.asyncio
    async def test_the_same_selection_is_not_attempted_twice_in_one_drain(
        self, platform: FakeRoom, mock_rest_client: MagicMock
    ) -> None:
        platform.post("m1")
        advanced_without_effect = AsyncMock(return_value=BacklogProcessResult.ADVANCED)

        with patch.object(
            ExecutionContext, "_run_message_attempt", advanced_without_effect
        ):
            async with started_room(platform, mock_rest_client) as room:
                await room.until(lambda: advanced_without_effect.await_count >= 1)
                await asyncio.sleep(0.05)
                attempts_in_first_drain = advanced_without_effect.await_count
                await room.ctx.request_resync()
                await room.until(lambda: advanced_without_effect.await_count > 1)

        assert attempts_in_first_drain == 1
