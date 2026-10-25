"""Room retirement bounds the wait for the actual ACP transport exit."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.retention import RoomRetentionConfig
from band.core.turn_lifecycle import TURN_SETTLE_GRACE_S
from tests.adapters.cursorturns.conftest import OpenCursor, cursor_session
from tests.adapters.cursorturns.kit import (
    ROOM_A,
    active_turn,
    busy_provider,
    cancels_sent,
    cursor_adapter,
    finish_turn,
    is_accepted,
    occupy,
    retire,
    terminated,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock

TRANSITION_S = 1.0
CONFIG = {
    "retention": RoomRetentionConfig(transition_timeout_s=TRANSITION_S),
    "inject_band_tools": False,
}

pytestmark = pytest.mark.timeout(10)


class TestCursorRetirementBound:
    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_a_transport_that_will_not_exit_fails_retirement_and_keeps_the_room(
        self,
    ) -> None:
        provider = busy_provider("prompt")
        teardown = provider.agent.exits_slowly()
        async with controlled_clock() as clock:
            resources = AsyncExitStack()
            try:
                session = await clock.run_io(
                    resources.enter_async_context(
                        cursor_session(provider.agent, **CONFIG)
                    )
                )
                await resources.enter_async_context(
                    background(await clock.run_io(occupy(session, provider)))
                )
                retiring = await resources.enter_async_context(
                    background(retire(cursor_adapter(session), ROOM_A))
                )
                await teardown.received.wait()
                assert not retiring.done()

                await clock.elapse(TRANSITION_S)
                with pytest.raises(CleanupIncompleteError):
                    await retiring
                assert not await is_accepted(session, room=ROOM_A)
            finally:
                teardown.release.set()
                provider.finish.set()
                await clock.run_io(resources.aclose())

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_a_retry_after_transport_exit_retires_the_room_and_frees_it(
        self,
    ) -> None:
        provider = busy_provider("prompt")
        teardown = provider.agent.exits_slowly()
        async with controlled_clock() as clock:
            resources = AsyncExitStack()
            try:
                session = await clock.run_io(
                    resources.enter_async_context(
                        cursor_session(provider.agent, **CONFIG)
                    )
                )
                sending = await resources.enter_async_context(
                    background(await clock.run_io(occupy(session, provider)))
                )
                retiring = await resources.enter_async_context(
                    background(retire(cursor_adapter(session), ROOM_A))
                )
                await teardown.received.wait()
                assert not retiring.done()

                await clock.elapse(TRANSITION_S)
                with pytest.raises(CleanupIncompleteError):
                    await retiring
                teardown.release.set()
                await retire(cursor_adapter(session), ROOM_A)
                await asyncio.gather(sending, return_exceptions=True)
                assert await is_accepted(session, room=ROOM_A)
            finally:
                teardown.release.set()
                provider.finish.set()
                await clock.run_io(resources.aclose())

    async def test_a_completed_turn_retires_without_a_grace_period(
        self, open_cursor: OpenCursor
    ) -> None:
        provider = busy_provider("prompt")
        session = await open_cursor(
            provider.agent, turn_settle_timeout_s=TURN_SETTLE_GRACE_S, **CONFIG
        )
        sending = await occupy(session, provider)
        turn = active_turn(session)
        try:
            await finish_turn(provider, sending)
            await terminated(session, turn)
            await retire(cursor_adapter(session), ROOM_A)
            assert cancels_sent(provider.agent) == []
            assert await is_accepted(session, room=ROOM_A)
        finally:
            provider.finish.set()
            await asyncio.gather(sending, return_exceptions=True)
