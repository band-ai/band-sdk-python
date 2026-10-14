"""Room retirement waits a bounded time for Cursor's provider task."""

from __future__ import annotations

import asyncio

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.turn_lifecycle import CYCLE_CANCEL_GRACE_SECONDS
from tests.adapters.cursorslot.conftest import OpenCursor
from tests.adapters.cursorslot.kit import (
    ROOM_A,
    ROOM_B,
    WAIT_S,
    busy_provider,
    cursor_adapter,
    hold_cancellation,
    is_accepted,
    occupy,
    retire,
)

# A retirement that waits unbounded fails here instead of hanging the suite.
RETIRE_GUARD_S = 10 * CYCLE_CANCEL_GRACE_SECONDS


class TestCursorRetirementBound:
    @pytest.mark.looptime
    async def test_a_turn_that_will_not_end_fails_retirement_and_keeps_the_slot(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        teardown = hold_cancellation(session)
        sending = await occupy(session, provider)

        try:
            async with asyncio.timeout(RETIRE_GUARD_S):
                with pytest.raises(CleanupIncompleteError):
                    await retire(cursor_adapter(session), ROOM_A)

            assert not await is_accepted(session, room=ROOM_B)
        finally:
            teardown.release.set()
            await asyncio.gather(sending, return_exceptions=True)

    @pytest.mark.looptime
    async def test_a_retry_after_the_turn_ends_retires_the_room_and_frees_the_slot(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(provider.agent)
        teardown = hold_cancellation(session)
        sending = await occupy(session, provider)

        try:
            async with asyncio.timeout(RETIRE_GUARD_S):
                with pytest.raises(CleanupIncompleteError):
                    await retire(cursor_adapter(session), ROOM_A)
        finally:
            teardown.release.set()
        async with asyncio.timeout(WAIT_S):
            await retire(cursor_adapter(session), ROOM_A)
        await asyncio.gather(sending, return_exceptions=True)

        assert await is_accepted(session, room=ROOM_B)
