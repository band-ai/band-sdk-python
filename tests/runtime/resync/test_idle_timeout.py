"""Tests for idle-timeout resync in Phase 2 loop."""

from __future__ import annotations

import random

from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event
from tests.runtime.conftest import wait_for_condition


class TestIdleTimeout:
    """Tests for idle-timeout resync in Phase 2 loop."""

    async def test_idle_timeout_triggers_resync(self, mock_link, mock_handler):
        """Phase 2 should call /next after idle_resync_seconds with no WS events."""
        config = SessionConfig(idle_resync_seconds=0.01)  # fast timeout for test
        ctx = ExecutionContext("room-1", mock_link, mock_handler, config=config)
        await ctx.start()

        await wait_for_condition(
            lambda: mock_link.get_next_message.call_count >= 2,
            timeout=1.0,
        )

        # get_next_message is called during Phase 1 AND during idle timeout resync
        assert mock_link.get_next_message.call_count >= 2

        await ctx.stop()

    async def test_websocket_work_does_not_force_an_immediate_poll(
        self, mock_link, mock_handler, monkeypatch
    ):
        monkeypatch.setattr(random, "uniform", lambda lower, upper: upper)
        config = SessionConfig(idle_resync_seconds=60)  # very long timeout
        ctx = ExecutionContext("room-1", mock_link, mock_handler, config=config)
        await ctx.start()
        await wait_for_condition(lambda: ctx.scheduler.sync_complete)

        call_count_after_phase1 = mock_link.get_next_message.call_count

        # New work restores the base interval without postponing the deadline.
        event = make_message_event(room_id="room-1", msg_id="msg-x")
        await ctx.on_event(event)
        await wait_for_condition(lambda: mock_handler.await_count >= 1)

        # No idle timeout should have fired (timeout is 60s)
        assert mock_link.get_next_message.call_count == call_count_after_phase1

        await ctx.stop()
