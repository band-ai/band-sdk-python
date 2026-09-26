"""``SessionConfig.release_idle_room_after_s``: idle room resource release.

Each wait is on an event the code under test sets (a turn starting, the
release callback running), never a bare sleep standing in for progress.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from band.runtime.execution import ExecutionContext, ExecutionState
from band.runtime.types import SessionConfig
from tests.runtime.conftest import make_link_mock, platform_msg

_RELEASE_AFTER_S = 0.05


class Room:
    """One ExecutionContext driven through /next, with recorded releases."""

    def __init__(
        self,
        room_id: str = "room-1",
        *,
        release_after_s: float | None = _RELEASE_AFTER_S,
        release_error: Exception | None = None,
    ) -> None:
        self.link = make_link_mock()
        self.pending: list[Any] = []

        async def next_message(*_args: Any, **_kwargs: Any) -> Any:
            return self.pending.pop(0) if self.pending else None

        self.link.get_next_message.side_effect = next_message
        self.link.get_stale_processing_messages = AsyncMock(return_value=[])
        self.turns: list[str] = []
        self.turn_started = asyncio.Event()
        self.turn_gate: asyncio.Event | None = None
        self.releases: list[str] = []
        self.released = asyncio.Event()
        self._release_error = release_error
        self.ctx = ExecutionContext(
            room_id,
            self.link,
            self._on_execute,
            config=SessionConfig(
                release_idle_room_after_s=release_after_s,
                idle_resync_seconds=30.0,
                enable_working_state=False,
            ),
            on_idle_release=self._on_release,
        )

    async def _on_execute(self, _ctx: Any, event: Any) -> None:
        self.turns.append(event.payload.id)
        self.turn_started.set()
        if self.turn_gate is not None:
            await self.turn_gate.wait()

    async def _on_release(self, room_id: str) -> None:
        self.releases.append(room_id)
        self.released.set()
        if self._release_error is not None:
            raise self._release_error

    async def send(self, msg_id: str) -> None:
        """Deliver one message through /next and wait until its turn starts."""
        self.turn_started.clear()
        self.pending.append(platform_msg(msg_id))
        await self.ctx.request_resync()
        await asyncio.wait_for(self.turn_started.wait(), timeout=5.0)

    async def wait_released(self) -> None:
        await asyncio.wait_for(self.released.wait(), timeout=5.0)
        self.released.clear()


@pytest.fixture
async def room():
    rooms: list[Room] = []

    def make(**kwargs: Any) -> Room:
        rooms.append(Room(**kwargs))
        return rooms[-1]

    yield make
    for r in rooms:
        await r.ctx.stop()


async def _started(r: Room) -> Room:
    await r.ctx.start()
    return r


async def test_an_idle_room_is_released_once_after_a_turn(room) -> None:
    r = await _started(room())
    await r.send("msg-1")

    await r.wait_released()

    assert r.releases == ["room-1"]
    assert r.ctx.state is ExecutionState.IDLE


async def test_the_next_turn_rearms_the_release(room) -> None:
    r = await _started(room())
    await r.send("msg-1")
    await r.wait_released()

    await r.send("msg-2")
    await r.wait_released()

    assert (r.turns, r.releases) == (["msg-1", "msg-2"], ["room-1", "room-1"])


async def test_a_room_that_never_ran_a_turn_is_left_alone(room) -> None:
    r = await _started(room())
    other = await _started(room(room_id="room-2"))
    await other.send("msg-1")
    await other.wait_released()

    assert (r.releases, other.releases) == ([], ["room-2"])


async def test_a_running_turn_is_never_released(room) -> None:
    r = await _started(room())
    r.turn_gate = asyncio.Event()
    await r.send("msg-1")

    # Several release periods pass mid-turn; the loop is busy with the turn,
    # so no release can be scheduled until it ends.
    await asyncio.sleep(_RELEASE_AFTER_S * 4)
    assert (r.ctx.state, r.releases) == (ExecutionState.PROCESSING, [])

    r.turn_gate.set()
    await r.wait_released()
    assert r.releases == ["room-1"]


async def test_a_message_queued_at_expiry_runs_before_any_release(room) -> None:
    r = await _started(room())
    r.turn_gate = asyncio.Event()
    await r.send("msg-1")
    r.turn_started.clear()
    r.pending.append(platform_msg("msg-2"))
    await r.ctx.request_resync()

    r.turn_gate.set()
    await asyncio.wait_for(r.turn_started.wait(), timeout=5.0)
    await r.wait_released()

    assert (r.turns, r.releases) == (["msg-1", "msg-2"], ["room-1"])


async def test_unset_config_never_releases(room) -> None:
    r = await _started(room(release_after_s=None))
    await r.send("msg-1")

    assert r.ctx._next_idle_wait() == (30.0, False)
    assert r.releases == []


async def test_a_failed_release_leaves_the_room_serving(room) -> None:
    r = await _started(room(release_error=RuntimeError("close failed")))
    await r.send("msg-1")
    await r.wait_released()

    await r.send("msg-2")

    assert r.turns == ["msg-1", "msg-2"]
    assert r.ctx.is_running


async def test_stop_before_expiry_leaves_no_pending_release(room) -> None:
    r = await _started(room(release_after_s=30.0))
    await r.send("msg-1")

    await r.ctx.stop()

    assert (r.ctx.is_running, r.releases) == (False, [])


def test_a_non_positive_setting_is_refused() -> None:
    with pytest.raises(ValueError, match="release_idle_room_after_s"):
        SessionConfig(release_idle_room_after_s=0)
