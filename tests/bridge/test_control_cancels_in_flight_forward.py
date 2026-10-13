"""Bridge control cancels in flight forward."""

from __future__ import annotations

import asyncio

from tests.bridge.conftest import FakeForwarder
from tests.bridge.kit import (
    HangingForwarder,
    await_cancelled,
    build_runner,
    control_signal,
    make_message_event,
)


class TestControlCancelsInFlightForward:
    async def test_interrupt_cancels_in_flight_forward(self) -> None:
        fwd = HangingForwarder()
        runner, _, _ = build_runner(forwarder=fwd)
        task = asyncio.create_task(
            runner._safe_handle_event(make_message_event(message_id="m1", room_id="r1"))
        )
        await fwd.started.wait()

        await runner._control.handle(
            control_signal("interrupt", scope="room", room_id="r1")
        )

        await await_cancelled(task)
        assert fwd.forwarded == []

    async def test_stop_cancels_in_flight_forward_identically(self) -> None:
        fwd = HangingForwarder()
        runner, _, _ = build_runner(forwarder=fwd)
        task = asyncio.create_task(
            runner._safe_handle_event(make_message_event(message_id="m1", room_id="r1"))
        )
        await fwd.started.wait()

        await runner._control.handle(control_signal("stop", scope="room", room_id="r1"))

        await await_cancelled(task)
        assert fwd.forwarded == []

    async def test_cancelled_message_is_not_remembered_as_forwarded(self) -> None:
        """A cancelled forward must stay retryable — never marked processed."""
        fwd = HangingForwarder()
        runner, _, _ = build_runner(forwarder=fwd)
        task = asyncio.create_task(
            runner._safe_handle_event(
                make_message_event(message_id="m-cancel", room_id="r1")
            )
        )
        await fwd.started.wait()

        await runner._control.handle(
            control_signal("interrupt", scope="room", room_id="r1")
        )
        await await_cancelled(task)

        assert "m-cancel" not in runner._processed_message_ids

    async def test_room_lock_releases_promptly_after_interrupt(self) -> None:
        """A second event for the same room must not wait on the cancelled task."""
        fwd = HangingForwarder()
        runner, _, _ = build_runner(forwarder=fwd)
        task1 = asyncio.create_task(
            runner._safe_handle_event(make_message_event(message_id="m1", room_id="r1"))
        )
        await fwd.started.wait()

        await runner._control.handle(
            control_signal("interrupt", scope="room", room_id="r1")
        )
        await await_cancelled(task1)

        fwd.hang = False
        await asyncio.wait_for(
            runner._safe_handle_event(
                make_message_event(message_id="m2", room_id="r1")
            ),
            timeout=1.0,
        )

        ids = {(p.get("payload") or {}).get("id") for p in fwd.forwarded}
        assert ids == {"m2"}

    async def test_interrupt_with_no_active_forward_is_noop(self) -> None:
        runner, fwd, _ = build_runner()

        await runner._control.handle(
            control_signal("interrupt", scope="room", room_id="r1")
        )

        assert isinstance(fwd, FakeForwarder)
        assert fwd.forwarded == []
        assert "r1" not in runner._active_room_tasks
