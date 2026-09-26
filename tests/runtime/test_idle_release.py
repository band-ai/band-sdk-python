"""``SessionConfig.release_idle_room_after_s``: idle room resource release.

Each wait is on an event the code under test sets (a turn starting, the
release callback running), never a bare sleep standing in for progress.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

from band.runtime.execution import ExecutionContext, ExecutionState
from band.runtime.types import SessionConfig
from tests.adapters.test_claude_sdk_idle_release import ROOM, claude_room
from tests.adapters.test_codex_adapter import (
    FakeCodexClient,
    _bootstrap_turn,
    _turn_completed,
    make_codex_adapter,
)
from tests.integrations.acp.acp_toolkit import FakeACPAgent, acp_adapter
from tests.runtime.conftest import make_link_mock, platform_msg, wait_for_condition

_RELEASE_AFTER_S = 0.05


class Room:
    """One ExecutionContext driven through /next, with recorded releases."""

    def __init__(
        self,
        room_id: str = "room-1",
        *,
        release_after_s: float | None = _RELEASE_AFTER_S,
        release_error: Exception | None = None,
        adapter_release: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.link = make_link_mock()
        self.release_gate: asyncio.Event | None = None
        self.release_finished = False
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
        self._adapter_release = adapter_release
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
        if self.release_gate is not None:
            await self.release_gate.wait()
            self.release_finished = True
        if self._release_error is not None:
            raise self._release_error
        if self._adapter_release is not None:
            await self._adapter_release(room_id)

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


async def test_stop_during_a_release_waits_for_its_teardown(room) -> None:
    r = await _started(room())
    r.release_gate = asyncio.Event()
    await r.send("msg-1")
    await r.wait_released()

    stopping = asyncio.create_task(r.ctx.stop())
    await wait_for_condition(lambda: not r.ctx.is_running, timeout=5.0)
    for _ in range(5):
        await asyncio.sleep(0)
    assert not stopping.done(), "stop must wait for the pending teardown"

    r.release_gate.set()
    await asyncio.wait_for(stopping, timeout=5.0)

    assert (r.release_finished, r.ctx.is_running) == (True, False)


async def test_cancelling_stop_mid_release_keeps_the_teardown_running(room) -> None:
    r = await _started(room())
    r.release_gate = asyncio.Event()
    await r.send("msg-1")
    await r.wait_released()
    stopping = asyncio.create_task(r.ctx.stop())
    await wait_for_condition(lambda: not r.ctx.is_running, timeout=5.0)

    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping

    assert r.ctx._release_task is not None, "the teardown must stay tracked"
    r.release_gate.set()
    await asyncio.wait_for(r.ctx.stop(), timeout=5.0)
    assert (r.release_finished, r.ctx._release_task) == (True, None)


class GatedCloseCodexClient(FakeCodexClient):
    """A Codex client whose close() waits for the test to let it finish."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.close_entered = asyncio.Event()
        self.close_gate = asyncio.Event()

    async def close(self) -> None:
        self.close_entered.set()
        await self.close_gate.wait()
        await super().close()


async def test_stopping_mid_codex_release_still_closes_the_app_server(room) -> None:
    client = GatedCloseCodexClient(events=[_turn_completed()])
    adapter = make_codex_adapter(client)
    await _bootstrap_turn(adapter)
    r = await _started(
        room(room_id="room-1", adapter_release=adapter.release_room_resources)
    )
    await r.send("msg-1")
    await asyncio.wait_for(client.close_entered.wait(), timeout=5.0)

    stopping = asyncio.create_task(r.ctx.stop())
    await wait_for_condition(lambda: not r.ctx.is_running, timeout=5.0)
    client.close_gate.set()
    await asyncio.wait_for(stopping, timeout=5.0)

    room_client = adapter._room_clients["room-1"]
    assert (client.closed, room_client.client) == (True, None)
    assert adapter._released_threads == {"room-1": "thr-1"}


async def test_stopping_mid_omp_release_still_stops_the_agent_process(room) -> None:
    agent = FakeACPAgent(supports_session_load=True).will_say("Noted.")
    async with acp_adapter(agent) as session:
        await session.send("My favorite color is blue.", bootstrap=True)
        runtime = session.adapter._runtimes["room-1"]
        stop_entered, stop_gate = asyncio.Event(), asyncio.Event()
        stop_completed = False
        real_stop = runtime.stop

        async def gated_stop() -> None:
            nonlocal stop_completed
            stop_entered.set()
            await stop_gate.wait()
            await real_stop()
            stop_completed = True

        runtime.stop = gated_stop  # type: ignore[method-assign]
        r = await _started(
            room(adapter_release=session.adapter._release_loadable_session)
        )
        await r.send("msg-1")
        await asyncio.wait_for(stop_entered.wait(), timeout=5.0)

        stopping = asyncio.create_task(r.ctx.stop())
        await wait_for_condition(lambda: not r.ctx.is_running, timeout=5.0)
        stop_gate.set()
        await asyncio.wait_for(stopping, timeout=5.0)

        assert stop_completed
        assert "room-1" not in session.adapter._runtimes


async def test_stopping_mid_claude_release_still_stops_the_session(
    room, tmp_path
) -> None:
    claude = await claude_room(str(tmp_path))
    claude.manager.cleanup_gate = asyncio.Event()
    r = await _started(room(adapter_release=claude.adapter.release_room_resources))
    await r.send("msg-1")
    await asyncio.wait_for(claude.manager.cleanup_entered.wait(), timeout=5.0)

    stopping = asyncio.create_task(r.ctx.stop())
    await wait_for_condition(lambda: not r.ctx.is_running, timeout=5.0)
    claude.manager.cleanup_gate.set()
    await asyncio.wait_for(stopping, timeout=5.0)

    assert claude.manager.has_session(ROOM) is False
    assert claude.adapter._released_sessions == {ROOM: "sess-1"}
    await claude.adapter.cleanup_all()
