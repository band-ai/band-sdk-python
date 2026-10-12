"""OneShotInvoker: shutdown settles accepted work before disposal."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.core.exceptions import (
    CleanupIncompleteError,
)
from band.runtime.oneshot import (
    OneShotInvoker,
)
from tests.runtime.conftest import make_link_mock, platform_msg
from tests.runtime.oneshot.kit import make_adapter_mock, msg_body


class AcceptedWork:
    """An invoker whose adapter work is held, with every effect ordered."""

    def __init__(self, *, turns: int = 1) -> None:
        self.order: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.turns = turns
        self.link = make_link_mock(next_messages=[platform_msg("msg-1"), None])
        self.link.mark_processed.side_effect = self.record("ack")
        self.link.disconnect.side_effect = self.record("disconnect")
        self.adapter = make_adapter_mock()
        self.adapter.on_event.side_effect = self.held("turn")
        self.adapter.on_cleanup.side_effect = self.held("room cleanup")
        self.adapter.cleanup_all.side_effect = self.record("cleanup_all")
        self.invoker = OneShotInvoker(
            link=self.link, adapter=self.adapter, agent_id="agent-1"
        )
        self._entered = 0

    def record(self, name: str):
        async def effect(*args: object, **kwargs: object) -> bool:
            self.order.append(name)
            return True

        return effect

    def held(self, name: str):
        async def effect(*args: object, **kwargs: object) -> None:
            self.order.append(f"{name} start")
            self._entered += 1
            if self._entered == self.turns:
                self.started.set()
            await self.release.wait()
            self.order.append(f"{name} end")

        return effect

    def accept(self, body: dict[str, Any]) -> asyncio.Task[dict[str, Any]]:
        return asyncio.create_task(self.invoker.handle_event(body))


ROOM_REMOVED = {"event_type": "room_removed", "room_id": "r1", "payload": {}}


async def run_ready_callbacks() -> None:
    """Yield without wall-clock delay, long enough for an unblocked shutdown
    to reach disposal."""
    for _ in range(20):
        await asyncio.sleep(0)


class TestShutdownDrainsAcceptedWork:
    """Admission, accepted work, settlement, then disposal."""

    @pytest.mark.parametrize(
        ("body", "settled"),
        [
            (msg_body(), ["turn start", "turn end", "ack"]),
            (ROOM_REMOVED, ["room cleanup start", "room cleanup end"]),
        ],
        ids=["turn", "room-cleanup"],
    )
    async def test_held_event_settles_before_disposal_and_refuses_new_events(
        self, body: dict[str, Any], settled: list[str]
    ) -> None:
        work = AcceptedWork()
        await work.invoker.startup()
        accepted = work.accept(body)
        await work.started.wait()

        stopping = asyncio.create_task(work.invoker.shutdown())
        await run_ready_callbacks()
        with pytest.raises(RuntimeError, match="shut down"):
            await work.invoker.handle_event(msg_body(msg_id="late"))
        assert "cleanup_all" not in work.order

        work.release.set()
        await asyncio.gather(accepted, stopping)
        assert work.order == [*settled, "cleanup_all", "disconnect"]

    async def test_shutdown_joins_every_accepted_event(self) -> None:
        work = AcceptedWork(turns=2)
        await work.invoker.startup()
        first = work.accept(msg_body(room_id="room-1"))
        second = work.accept(ROOM_REMOVED)
        await work.started.wait()
        stopping = asyncio.create_task(work.invoker.shutdown())
        await run_ready_callbacks()
        assert "cleanup_all" not in work.order

        work.release.set()
        await asyncio.gather(first, second, stopping)
        assert work.order[-2:] == ["cleanup_all", "disconnect"]
        assert work.order.count("turn end") == work.order.count("room cleanup end") == 1

    async def test_cancelled_and_failed_events_are_still_waited_for(self) -> None:
        work = AcceptedWork(turns=2)
        unwinding = asyncio.Event()

        async def slow_unwind(*args: object) -> None:
            work.order.append("turn start")
            work.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await unwinding.wait()
                work.order.append("turn unwound")
                raise

        async def failing_cleanup(room_id: str) -> None:
            work.order.append("room cleanup failed")
            raise OSError("cleanup failed")

        work.adapter.on_event.side_effect = slow_unwind
        work.adapter.on_cleanup.side_effect = failing_cleanup
        await work.invoker.startup()
        turn = work.accept(msg_body())
        await work.started.wait()
        with pytest.raises(OSError, match="cleanup failed"):
            await work.invoker.handle_event(ROOM_REMOVED)
        turn.cancel()
        stopping = asyncio.create_task(work.invoker.shutdown())
        await run_ready_callbacks()
        assert "cleanup_all" not in work.order

        unwinding.set()
        with pytest.raises(asyncio.CancelledError):
            await turn
        await stopping
        assert work.order[-3:] == ["turn unwound", "cleanup_all", "disconnect"]

    async def test_cancelled_shutdown_waiter_keeps_the_drain(self) -> None:
        work = AcceptedWork()
        await work.invoker.startup()
        accepted = work.accept(msg_body())
        await work.started.wait()
        waiter = asyncio.create_task(work.invoker.shutdown())
        await run_ready_callbacks()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        work.release.set()
        await accepted
        await work.invoker.shutdown()
        assert work.order[:5] == [
            "turn start",
            "turn end",
            "ack",
            "cleanup_all",
            "disconnect",
        ]
        assert work.order.count("cleanup_all") == 1

    async def test_shutdown_from_an_accepted_event_does_not_self_join(self) -> None:
        work = AcceptedWork()
        requested: list[type[BaseException]] = []

        async def turn_requests_shutdown(*args: object) -> None:
            work.order.append("turn start")
            try:
                await work.invoker.shutdown()
            except CleanupIncompleteError as error:
                requested.append(type(error))
            work.order.append("turn end")

        work.adapter.on_event.side_effect = turn_requests_shutdown
        await work.invoker.startup()
        await work.invoker.handle_event(msg_body())
        await work.invoker.shutdown()
        assert requested == [CleanupIncompleteError]
        assert work.order == [
            "turn start",
            "turn end",
            "ack",
            "cleanup_all",
            "disconnect",
        ]
