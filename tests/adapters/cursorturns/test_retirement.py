"""Room retirement waits a bounded time for Cursor's provider task."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.retention import RoomRetentionConfig
from tests.adapters.cursorturns.conftest import OpenCursor
from tests.adapters.cursorturns.kit import (
    ROOM_A,
    WAIT_S,
    busy_provider,
    cursor_adapter,
    hold_cancellation,
    is_accepted,
    occupy,
    retire,
)

# Short enough to outlast, long enough for a cancellation that is not held.
TRANSITION_S = 0.05
CONFIG = {
    "retention": RoomRetentionConfig(transition_timeout_s=TRANSITION_S),
    # The shared Band MCP server closes within the same bound, which a real
    # server cannot meet this short.
    "inject_band_tools": False,
}


class TestCursorRetirementBound:
    async def test_a_turn_that_will_not_end_fails_retirement_and_keeps_the_room(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent, **CONFIG)
        teardown = hold_cancellation(session)
        sending = await occupy(session, provider)

        try:
            async with asyncio.timeout(WAIT_S):
                with pytest.raises(CleanupIncompleteError):
                    await retire(cursor_adapter(session), ROOM_A)

            assert not await is_accepted(session, room=ROOM_A)
        finally:
            teardown.release.set()
            await asyncio.gather(sending, return_exceptions=True)

    async def test_a_retry_after_the_turn_ends_retires_the_room_and_frees_it(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent, **CONFIG)
        teardown = hold_cancellation(session)
        sending = await occupy(session, provider)

        try:
            async with asyncio.timeout(WAIT_S):
                with pytest.raises(CleanupIncompleteError):
                    await retire(cursor_adapter(session), ROOM_A)
        finally:
            teardown.release.set()
        async with asyncio.timeout(WAIT_S):
            await retire(cursor_adapter(session), ROOM_A)
        await asyncio.gather(sending, return_exceptions=True)

        assert await is_accepted(session, room=ROOM_A)
