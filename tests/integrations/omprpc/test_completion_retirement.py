"""An expired turn stays closed until surviving host work actually retires."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpCompletionTimeout, OmpProcessError
from band.integrations.omprpc.custody import process_is_alive
from band.integrations.ompwire import OmpEventType
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omprpc.callbacks import (
    StubbornToolObserver,
)
from tests.integrations.omprpc.completion import (
    COMPLETION_BUDGET_S,
    NO_CALLBACK_GRACE_S,
    CompletionContext,
    snapshots_observed,
)
from tests.integrations.omprpc.events import follow_up_texts
from tests.integrations.omprpc.processes import peer_process
from tests.toolkit.clock import binary_clock_resolution


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestCompletionRetirement:
    async def test_timeout_keeps_custody_until_callback_release_then_recovers(
        self, tool_completion_turn: CompletionContext[StubbornToolObserver]
    ) -> None:
        async with tool_completion_turn as turn:
            await turn.observer.entered.wait()
            await turn.observer.wait_for(OmpEventType.AGENT_END)
            await snapshots_observed(turn.client)
            turn.client.callback_exit_timeout_s = NO_CALLBACK_GRACE_S

            await turn.clock.elapse(COMPLETION_BUDGET_S)
            with pytest.raises(OmpCompletionTimeout, match="completion stayed idle"):
                await turn.clock.run_io(turn.running)
            assert turn.observer.cancel_seen.is_set()
            assert not turn.observer.finished.is_set()
            assert not await asyncio.to_thread(process_is_alive, turn.peer)
            with pytest.raises(OmpProcessError):
                await turn.client.start()

            await turn.observer.release()
            await turn.observer.retired.wait()
            await turn.clock.run_io(turn.client.close())
            await turn.clock.run_io(turn.client.start())
            assert await peer_process(turn.client) != turn.peer
            assert await turn.clock.run_io(follow_up_texts(turn.client)) == [COMPLETE]
