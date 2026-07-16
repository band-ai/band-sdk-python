"""Tests for AgentRunner control-signal handling (interrupt/stop/play).

The bridge holds no Band lifecycle logic, so interrupt and stop are handled
identically here: cancel whatever forward task is currently in flight for the
target room(s), if any. The interrupt-vs-stop distinction (consume vs.
leave-for-replay) is handled downstream by the container via ``/next``, not
by the bridge. ``play`` proactively nudges the room(s) via ``/next`` so a
queued message is picked up without waiting for the next natural bridge
event.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.client.streaming import AgentControlPayload

from .conftest import FakeForwarder
from .test_bridge import _build_runner, _make_message_event, _make_platform_message

pytestmark = pytest.mark.asyncio


def _control(
    mode: str, *, scope: str, room_id: str | None, **kw: Any
) -> AgentControlPayload:
    return AgentControlPayload(
        mode=mode, scope=scope, agent_id="agent-1", room_id=room_id, **kw
    )


class _HangingForwarder:
    """Hangs on the first forward() until cancelled; forwards normally after.

    ``started`` lets a test wait until the forward call has actually begun
    before issuing a control signal, avoiding a race between task creation
    and the signal arriving before there's anything in flight to cancel.
    """

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.hang = True
        self.forwarded: list[dict[str, Any]] = []

    async def forward(self, payload: dict[str, Any]) -> None:
        if self.hang:
            self.started.set()
            await asyncio.sleep(120)
        self.forwarded.append(payload)

    async def close(self) -> None:
        return


class _MultiRoomHangingForwarder:
    """Hangs forever, independently, for each of a fixed set of rooms."""

    def __init__(self, rooms: list[str]) -> None:
        self.started: dict[str, asyncio.Event] = {r: asyncio.Event() for r in rooms}
        self.forwarded: list[dict[str, Any]] = []

    async def forward(self, payload: dict[str, Any]) -> None:
        room_id = payload.get("room_id")
        self.started[room_id].set()
        await asyncio.sleep(120)
        self.forwarded.append(payload)

    async def close(self) -> None:
        return


async def _await_cancelled(task: asyncio.Task[None]) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled()


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class TestControlWiring:
    async def test_connect_wires_on_control_to_handler(self) -> None:
        runner, _, link = _build_runner()
        link.__anext__ = AsyncMock(side_effect=StopAsyncIteration())

        await runner._connect_and_consume()

        assert link.on_control == runner._handle_control


# ---------------------------------------------------------------------------
# Interrupt / stop — cancel an in-flight forward
# ---------------------------------------------------------------------------


class TestControlCancelsInFlightForward:
    async def test_interrupt_cancels_in_flight_forward(self) -> None:
        fwd = _HangingForwarder()
        runner, _, _ = _build_runner(forwarder=fwd)
        task = asyncio.create_task(
            runner._safe_handle_event(
                _make_message_event(message_id="m1", room_id="r1")
            )
        )
        await fwd.started.wait()

        await runner._handle_control(_control("interrupt", scope="room", room_id="r1"))

        await _await_cancelled(task)
        assert fwd.forwarded == []

    async def test_stop_cancels_in_flight_forward_identically(self) -> None:
        fwd = _HangingForwarder()
        runner, _, _ = _build_runner(forwarder=fwd)
        task = asyncio.create_task(
            runner._safe_handle_event(
                _make_message_event(message_id="m1", room_id="r1")
            )
        )
        await fwd.started.wait()

        await runner._handle_control(_control("stop", scope="room", room_id="r1"))

        await _await_cancelled(task)
        assert fwd.forwarded == []

    async def test_cancelled_message_is_not_remembered_as_forwarded(self) -> None:
        """A cancelled forward must stay retryable — never marked processed."""
        fwd = _HangingForwarder()
        runner, _, _ = _build_runner(forwarder=fwd)
        task = asyncio.create_task(
            runner._safe_handle_event(
                _make_message_event(message_id="m-cancel", room_id="r1")
            )
        )
        await fwd.started.wait()

        await runner._handle_control(_control("interrupt", scope="room", room_id="r1"))
        await _await_cancelled(task)

        assert "m-cancel" not in runner._processed_message_ids

    async def test_room_lock_releases_promptly_after_interrupt(self) -> None:
        """A second event for the same room must not wait on the cancelled task."""
        fwd = _HangingForwarder()
        runner, _, _ = _build_runner(forwarder=fwd)
        task1 = asyncio.create_task(
            runner._safe_handle_event(
                _make_message_event(message_id="m1", room_id="r1")
            )
        )
        await fwd.started.wait()

        await runner._handle_control(_control("interrupt", scope="room", room_id="r1"))
        await _await_cancelled(task1)

        fwd.hang = False
        await asyncio.wait_for(
            runner._safe_handle_event(
                _make_message_event(message_id="m2", room_id="r1")
            ),
            timeout=1.0,
        )

        ids = {(p.get("payload") or {}).get("id") for p in fwd.forwarded}
        assert ids == {"m2"}

    async def test_interrupt_with_no_active_forward_is_noop(self) -> None:
        runner, fwd, _ = _build_runner()

        await runner._handle_control(_control("interrupt", scope="room", room_id="r1"))

        assert isinstance(fwd, FakeForwarder)
        assert fwd.forwarded == []
        assert "r1" not in runner._active_room_tasks


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


class TestControlRouting:
    async def test_room_scope_only_cancels_target_room(self) -> None:
        fwd = _MultiRoomHangingForwarder(rooms=["r1", "r2"])
        runner, _, _ = _build_runner(forwarder=fwd)
        t1 = asyncio.create_task(
            runner._safe_handle_event(
                _make_message_event(message_id="m1", room_id="r1")
            )
        )
        t2 = asyncio.create_task(
            runner._safe_handle_event(
                _make_message_event(message_id="m2", room_id="r2")
            )
        )
        await fwd.started["r1"].wait()
        await fwd.started["r2"].wait()

        await runner._handle_control(_control("interrupt", scope="room", room_id="r1"))

        await _await_cancelled(t1)
        assert not t2.done()

        t2.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t2

    async def test_agent_scope_null_room_cancels_all_active_forwards(self) -> None:
        fwd = _MultiRoomHangingForwarder(rooms=["r1", "r2"])
        runner, _, link = _build_runner(forwarder=fwd)
        link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            return_value=MagicMock(data=[MagicMock(id="r1"), MagicMock(id="r2")])
        )
        t1 = asyncio.create_task(
            runner._safe_handle_event(
                _make_message_event(message_id="m1", room_id="r1")
            )
        )
        t2 = asyncio.create_task(
            runner._safe_handle_event(
                _make_message_event(message_id="m2", room_id="r2")
            )
        )
        await fwd.started["r1"].wait()
        await fwd.started["r2"].wait()

        await runner._handle_control(_control("interrupt", scope="agent", room_id=None))

        await _await_cancelled(t1)
        await _await_cancelled(t2)

    async def test_bad_scope_with_no_room_id_is_noop(self) -> None:
        runner, fwd, _ = _build_runner()

        await runner._handle_control(
            AgentControlPayload(mode="interrupt", scope="room", agent_id="agent-1")
        )

        assert isinstance(fwd, FakeForwarder)
        assert fwd.forwarded == []


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


class TestControlDedup:
    async def test_duplicate_correlation_id_does_not_clobber_a_later_task(self) -> None:
        """The real risk dedup guards against: a stale duplicate delivery must
        not reach out and cancel whatever unrelated new task now occupies the
        same room slot."""
        fwd = _HangingForwarder()
        runner, _, _ = _build_runner(forwarder=fwd)
        sig = _control("interrupt", scope="room", room_id="r1", correlation_id="ctl-1")

        task1 = asyncio.create_task(
            runner._safe_handle_event(
                _make_message_event(message_id="m1", room_id="r1")
            )
        )
        await fwd.started.wait()
        await runner._handle_control(sig)
        await _await_cancelled(task1)

        # A new, unrelated forward now occupies the room slot.
        fwd.started = asyncio.Event()
        fwd.hang = True
        task2 = asyncio.create_task(
            runner._safe_handle_event(
                _make_message_event(message_id="m2", room_id="r1")
            )
        )
        await fwd.started.wait()

        # Duplicate delivery of the SAME signal must not cancel task2.
        await runner._handle_control(sig)
        await asyncio.sleep(0.01)
        assert not task2.done()

        task2.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task2

    async def test_duplicate_correlation_id_dropped(self) -> None:
        runner, _, _ = _build_runner()
        runner._cancel_active_forward = MagicMock()
        sig = _control("interrupt", scope="room", room_id="r1", correlation_id="ctl-1")

        await runner._handle_control(sig)
        await runner._handle_control(sig)

        runner._cancel_active_forward.assert_called_once()

    async def test_distinct_correlation_ids_both_applied(self) -> None:
        runner, _, _ = _build_runner()
        runner._cancel_active_forward = MagicMock()

        await runner._handle_control(
            _control("interrupt", scope="room", room_id="r1", correlation_id="ctl-1")
        )
        await runner._handle_control(
            _control("interrupt", scope="room", room_id="r1", correlation_id="ctl-2")
        )

        assert runner._cancel_active_forward.call_count == 2

    async def test_missing_correlation_id_not_deduped(self) -> None:
        runner, _, _ = _build_runner()
        runner._cancel_active_forward = MagicMock()
        sig = _control("interrupt", scope="room", room_id="r1")

        await runner._handle_control(sig)
        await runner._handle_control(sig)

        assert runner._cancel_active_forward.call_count == 2


# ---------------------------------------------------------------------------
# Play — proactive /next nudge
# ---------------------------------------------------------------------------


class TestControlPlayNudge:
    async def test_room_scope_play_nudges_next_message(self) -> None:
        runner, fwd, link = _build_runner()
        link.get_next_message = AsyncMock(
            return_value=_make_platform_message("m-a", "r1")
        )

        await runner._handle_control(_control("play", scope="room", room_id="r1"))

        assert isinstance(fwd, FakeForwarder)
        ids = {(p.get("payload") or {}).get("id") for p in fwd.forwarded}
        assert ids == {"m-a"}

    async def test_agent_scope_play_nudges_all_rooms(self) -> None:
        runner, fwd, link = _build_runner()
        link.rest.agent_api_chats.list_agent_chats = AsyncMock(
            return_value=MagicMock(data=[MagicMock(id="r1"), MagicMock(id="r2")])
        )
        link.get_next_message.side_effect = [
            _make_platform_message("m-a", "r1"),
            _make_platform_message("m-b", "r2"),
        ]

        await runner._handle_control(_control("play", scope="agent", room_id=None))

        assert isinstance(fwd, FakeForwarder)
        ids = {(p.get("payload") or {}).get("id") for p in fwd.forwarded}
        assert ids == {"m-a", "m-b"}

    async def test_play_with_no_backlog_forwards_nothing(self) -> None:
        runner, fwd, link = _build_runner()
        link.get_next_message = AsyncMock(return_value=None)

        await runner._handle_control(_control("play", scope="room", room_id="r1"))

        assert isinstance(fwd, FakeForwarder)
        assert fwd.forwarded == []


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


class TestControlGracefulDegradation:
    async def test_unknown_mode_combinations_never_raise(self) -> None:
        """Defensive: any well-formed payload must not raise, even degenerate
        combinations (e.g. play for a room with nothing pending)."""
        runner, _, link = _build_runner()
        link.get_next_message = AsyncMock(return_value=None)

        for mode in ("interrupt", "stop", "play"):
            await runner._handle_control(
                _control(mode, scope="room", room_id="ghost-room")
            )
