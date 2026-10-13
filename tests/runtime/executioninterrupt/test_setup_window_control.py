"""Controls that land while a turn is still in setup, before its handler."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from band.platform.event import MessageEvent
from band.runtime.execution import ExecutionContext
from tests.conftest import make_message_event
from tests.runtime.executioninterrupt.kit import auxiliary_context
from tests.runtime.helpers import (
    ROOM_ID,
    LifecyclePlatform,
    ResponseGate,
    contact_event,
    rest_client_over,
)

LIVE = "live-1"
CONTACT = "contact-1"


class TestControlDuringTurnSetup:
    """A control during participant hydration ends the turn it lands in."""

    @staticmethod
    def recording(
        invoked: list[str],
    ) -> Callable[[ExecutionContext, MessageEvent], Awaitable[None]]:
        async def handler(ctx: ExecutionContext, event: MessageEvent) -> None:
            invoked.append(event.payload.id)

        return handler

    @asynccontextmanager
    async def live_turn_controlled_in_setup(
        self,
        peer: LifecyclePlatform,
        invoked: list[str],
        control: Callable[[ExecutionContext], object],
    ) -> AsyncIterator[ExecutionContext]:
        """Run a live message whose hydration ``control`` lands in, to its end."""
        peer.add_message(LIVE)
        gate = ResponseGate(peer, "/participants")
        async with rest_client_over(gate.answer) as rest:
            ctx = auxiliary_context(rest, self.recording(invoked))
            try:
                turn = asyncio.create_task(
                    ctx._process_event(make_message_event(room_id=ROOM_ID, msg_id=LIVE))
                )
                await gate.entered.wait()
                control(ctx)
                gate.release.set()
                await turn
                yield ctx
            finally:
                await ctx.stop()

    async def test_stop_during_live_setup_holds_the_message_for_play(self) -> None:
        peer = LifecyclePlatform()
        invoked: list[str] = []
        async with self.live_turn_controlled_in_setup(
            peer, invoked, ExecutionContext.stop_room
        ) as ctx:
            assert invoked == []
            assert peer.marked("processed") == []

            await ctx.resume_room()
            await ctx._wait_until_resync_complete()
            assert invoked == [LIVE]
            assert peer.marked("processed") == [LIVE]

    async def test_interrupt_during_live_setup_consumes_the_message(self) -> None:
        peer = LifecyclePlatform()
        invoked: list[str] = []
        interrupted: list[bool] = []
        async with self.live_turn_controlled_in_setup(
            peer, invoked, lambda ctx: interrupted.append(ctx.interrupt())
        ):
            assert interrupted == [True]
            assert invoked == []
            assert peer.marked("processed") == [LIVE]

    async def test_stop_during_synthetic_setup_retains_the_event_for_play(
        self,
    ) -> None:
        peer = LifecyclePlatform()
        gate = ResponseGate(peer, "/participants")
        invoked: list[str] = []
        async with rest_client_over(gate.answer) as rest:
            ctx = auxiliary_context(rest, self.recording(invoked))
            try:
                turn = asyncio.create_task(ctx._process_event(contact_event(CONTACT)))
                await gate.entered.wait()
                ctx.stop_room()
                gate.release.set()
                await turn
                assert invoked == []

                await ctx.resume_room()
                await ctx._wait_until_resync_complete()
                assert invoked == [CONTACT]
            finally:
                await ctx.stop()

    async def test_interrupt_after_a_processed_skip_finds_no_turn(self) -> None:
        peer = LifecyclePlatform()
        peer.add_processed_history(LIVE)
        invoked: list[str] = []
        async with rest_client_over(peer.answer) as rest:
            ctx = auxiliary_context(
                rest, self.recording(invoked), enable_context_hydration=True
            )
            try:
                assert await ctx._process_event(
                    make_message_event(room_id=ROOM_ID, msg_id=LIVE)
                )
                assert invoked == []
                assert ctx.interrupt() is False
            finally:
                await ctx.stop()
