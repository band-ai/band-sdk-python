"""Bridge control cancels in flight forward."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import pytest

from tests.bridge.conftest import FakeForwarder
from tests.bridge.kit import (
    GatedForwarder,
    HangingForwarder,
    Mark,
    accept_event,
    await_cancelled,
    build_runner,
    control_signal,
    make_message_event,
    retained_rooms,
)


class TestControlCancelsInFlightForward:
    @pytest.mark.parametrize("concurrency", [1, 32])
    async def test_interrupt_cancels_holder_and_room_waiter(
        self, concurrency: int
    ) -> None:
        forwarder = GatedForwarder(("a",))
        runner, _, _ = build_runner(
            forwarder=forwarder, max_concurrent_forwards=concurrency
        )
        async with asyncio.TaskGroup() as group, AsyncExitStack() as cleanup:
            cleanup.callback(forwarder.release["a"].set)
            a = group.create_task(runner._safe_handle_event(make_message_event("a")))
            await forwarder.started["a"].wait()
            accepted = asyncio.Event()
            b = group.create_task(
                accept_event(runner, make_message_event("b"), accepted)
            )
            await accepted.wait()
            await runner._control.handle(
                control_signal("interrupt", scope="room", room_id="r1")
            )
            await await_cancelled(a)
            await await_cancelled(b)
            assert forwarder.events == [(Mark.START, "a")]
            assert retained_rooms(runner) == set()
            await runner._safe_handle_event(make_message_event("fresh"))
        assert forwarder.events == [
            (Mark.START, "a"),
            (Mark.START, "fresh"),
            (Mark.END, "fresh"),
        ]

    async def test_interrupt_cancels_waiter_for_global_capacity(self) -> None:
        forwarder = GatedForwarder(("a",))
        runner, _, _ = build_runner(forwarder=forwarder, max_concurrent_forwards=1)
        async with asyncio.TaskGroup() as group, AsyncExitStack() as cleanup:
            cleanup.callback(forwarder.release["a"].set)
            a = group.create_task(
                runner._safe_handle_event(make_message_event("a", "r1"))
            )
            await forwarder.started["a"].wait()
            accepted = asyncio.Event()
            b = group.create_task(
                accept_event(runner, make_message_event("b", "r2"), accepted)
            )
            await accepted.wait()
            await runner._control.handle(
                control_signal("interrupt", scope="room", room_id="r2")
            )
            await await_cancelled(b)
            assert retained_rooms(runner) == {"r1"}
            assert forwarder.events == [(Mark.START, "a")]
            forwarder.release["a"].set()
            await a
            await runner._safe_handle_event(make_message_event("fresh", "r2"))
        assert forwarder.events == [
            (Mark.START, "a"),
            (Mark.END, "a"),
            (Mark.START, "fresh"),
            (Mark.END, "fresh"),
        ]
        assert retained_rooms(runner) == set()

    async def test_new_work_waits_for_cancelled_handler_cleanup(self) -> None:
        forwarder = GatedForwarder(("a",), held_cleanup=("a",))
        runner, _, _ = build_runner(forwarder=forwarder)
        async with asyncio.TaskGroup() as group, AsyncExitStack() as cleanup:
            cleanup.callback(forwarder.release_cleanup["a"].set)
            a = group.create_task(runner._safe_handle_event(make_message_event("a")))
            await forwarder.started["a"].wait()
            await runner._control.handle(
                control_signal("interrupt", scope="room", room_id="r1")
            )
            await forwarder.settling["a"].wait()
            accepted = asyncio.Event()
            b = group.create_task(
                accept_event(runner, make_message_event("b"), accepted)
            )
            await accepted.wait()
            assert retained_rooms(runner) == {"r1"}
            assert forwarder.events == [(Mark.START, "a")]
            forwarder.release_cleanup["a"].set()
            await await_cancelled(a)
            await b
        assert forwarder.events == [
            (Mark.START, "a"),
            (Mark.START, "b"),
            (Mark.END, "b"),
        ]
        assert retained_rooms(runner) == set()

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
        assert retained_rooms(runner) == set()
