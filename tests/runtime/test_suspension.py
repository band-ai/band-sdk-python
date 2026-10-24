"""Consumer suspension barriers follow room work through startup and shutdown."""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import DeliveryStatus
from band.runtime.execution import ExecutionContext
from tests.runtime.admission.barriers import OFF, Call, Model, Platform, started_room
from tests.runtime.batching.kit import Turns
from tests.runtime.conftest import Waiting
from tests.runtime.delivery.kit import RestPeer, room_over, started_session

pytestmark = pytest.mark.looptime


async def observe_suspension(ctx: ExecutionContext, entered: asyncio.Event) -> None:
    """Signal that the waiter has entered before observing its blocked state."""
    entered.set()
    await Waiting.suspended(ctx)


class TestSuspension:
    async def test_startup_and_queued_input_finish_before_suspension(self) -> None:
        peer = RestPeer()
        peer.post("backlog")
        async with started_session(peer) as session:
            await session.quiesce()
            await session.deliver(peer.post("live"))
            await session.quiesce()

            assert session.turns.ids == [("backlog",), ("live",)]
            assert peer.status_of("live") is DeliveryStatus.PROCESSED

    async def test_a_startup_read_must_finish_before_suspension(self) -> None:
        journal: list[str] = []
        platform = Platform(journal=journal)
        model = Model(journal=journal)
        platform.post("backlog")
        held = platform.hold(Call.next())
        async with started_room(OFF, platform=platform, model=model) as room:
            await room.reached(held)
            entered = asyncio.Event()
            async with asyncio.TaskGroup() as tasks:
                waiting = tasks.create_task(observe_suspension(room.ctx, entered))
                await entered.wait()
                assert not waiting.done()
                held.release()
                await waiting

            assert model.ids == [("backlog",)]
            assert platform.statuses == {"backlog": DeliveryStatus.PROCESSED}

    async def test_a_busy_pass_finishes_after_its_handler_is_released(self) -> None:
        peer = RestPeer()
        async with started_session(peer, gate=True) as session:
            await session.deliver(peer.post("busy"))
            await session.until(lambda: session.turns.ids == [("busy",)])
            entered = asyncio.Event()
            async with asyncio.TaskGroup() as tasks:
                waiting = tasks.create_task(observe_suspension(session.ctx, entered))
                await entered.wait()
                assert not waiting.done()
                session.turns.release.set()
                await waiting

            assert peer.status_of("busy") is DeliveryStatus.PROCESSED

    async def test_a_stopped_room_suspends_with_input_retained(self) -> None:
        peer = RestPeer()
        async with started_session(peer) as session:
            await session.quiesce()
            session.ctx.stop_room()
            await session.deliver(peer.post("held"))
            await session.quiesce()
            stopped = session.progress
            await session.ctx.resume_room()
            await session.quiesce()

            assert stopped == ([], [])
            assert peer.status_of("held") is DeliveryStatus.PROCESSED

    async def test_a_closed_consumer_does_not_have_to_drain_retained_input(
        self,
    ) -> None:
        peer = RestPeer()
        turns = Turns()
        async with room_over(peer, turns.single) as ctx:
            await ctx.start()
            await Waiting.suspended(ctx)
            await ctx.stop()
            await ctx.on_event(peer.post("retained"))
            await Waiting.suspended(ctx)

            assert turns.ids == []
            assert peer.status_of("retained") is None
