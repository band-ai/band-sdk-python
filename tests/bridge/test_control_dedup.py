"""Bridge control dedup."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import MagicMock

import pytest

from tests.bridge.kit import (
    HangingForwarder,
    await_cancelled,
    build_runner,
    control_signal,
    make_message_event,
)

pytestmark = pytest.mark.asyncio


class TestControlDedup:
    async def test_duplicate_correlation_id_does_not_clobber_a_later_task(self) -> None:
        """The real risk dedup guards against: a stale duplicate delivery must
        not reach out and cancel whatever unrelated new task now occupies the
        same room slot."""
        fwd = HangingForwarder()
        runner, _, _ = build_runner(forwarder=fwd)
        sig = control_signal(
            "interrupt", scope="room", room_id="r1", correlation_id="ctl-1"
        )

        task1 = asyncio.create_task(
            runner._safe_handle_event(make_message_event(message_id="m1", room_id="r1"))
        )
        await fwd.started.wait()
        await runner._control.handle(sig)
        await await_cancelled(task1)

        # A new, unrelated forward now occupies the room slot.
        fwd.started = asyncio.Event()
        fwd.hang = True
        task2 = asyncio.create_task(
            runner._safe_handle_event(make_message_event(message_id="m2", room_id="r1"))
        )
        await fwd.started.wait()

        # Duplicate delivery of the SAME signal must not cancel task2.
        await runner._control.handle(sig)
        await asyncio.sleep(0.01)
        assert not task2.done()

        task2.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task2

    async def test_duplicate_correlation_id_dropped(self) -> None:
        runner, _, _ = build_runner()
        runner._cancel_active_forward = MagicMock()
        sig = control_signal(
            "interrupt", scope="room", room_id="r1", correlation_id="ctl-1"
        )

        await runner._control.handle(sig)
        await runner._control.handle(sig)

        runner._cancel_active_forward.assert_called_once()

    async def test_distinct_correlation_ids_both_applied(self) -> None:
        runner, _, _ = build_runner()
        runner._cancel_active_forward = MagicMock()

        await runner._control.handle(
            control_signal(
                "interrupt", scope="room", room_id="r1", correlation_id="ctl-1"
            )
        )
        await runner._control.handle(
            control_signal(
                "interrupt", scope="room", room_id="r1", correlation_id="ctl-2"
            )
        )

        assert runner._cancel_active_forward.call_count == 2

    async def test_missing_correlation_id_not_deduped(self) -> None:
        runner, _, _ = build_runner()
        runner._cancel_active_forward = MagicMock()
        sig = control_signal("interrupt", scope="room", room_id="r1")

        await runner._control.handle(sig)
        await runner._control.handle(sig)

        assert runner._cancel_active_forward.call_count == 2
