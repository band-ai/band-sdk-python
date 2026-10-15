"""Running room boundary scenarios over the generated REST client's HTTP peer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

from band.client.streaming import DeliveryStatus
from band.core.types import (
    SYNTHETIC_CONTACT_EVENTS_SENDER_ID,
    SYNTHETIC_SENDER_TYPE,
)
from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.conftest import make_message_event, make_participant_added_event
from tests.platform.settlement.kit import ROOM_ID, link_over
from tests.runtime.batching.kit import Turns
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import AGENT_ID
from tests.runtime.recovery.kit import EPOCH, RoomPeer, Row

WARMUP = "warmup"
BEFORE = "ffffffff-ffff-4fff-8fff-ffffffffffff"
AFTER = "00000000-0000-4000-8000-000000000001"
LAST_BEFORE = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
UNKNOWN = "efffffff-ffff-4fff-8fff-ffffffffffff"
SYNTHETIC = "synthetic"


class BoundaryPeer(RoomPeer):
    def post_at(self, message_id: str, second: float) -> MessageEvent:
        self.rows[message_id] = Row(message_id, EPOCH + timedelta(seconds=second))
        return self.event(message_id)


def local_boundary(*, synthetic: bool) -> PlatformEvent:
    if synthetic:
        return make_message_event(
            room_id=ROOM_ID,
            msg_id=SYNTHETIC,
            sender_id=SYNTHETIC_CONTACT_EVENTS_SENDER_ID,
            sender_type=SYNTHETIC_SENDER_TYPE,
        )
    return make_participant_added_event(room_id=ROOM_ID)


@dataclass
class BoundaryRoom:
    peer: BoundaryPeer
    ctx: ExecutionContext
    turns: Turns

    async def deliver(self, *events: PlatformEvent) -> None:
        for event in events:
            await self.ctx.on_event(event)

    async def settle(self) -> None:
        self.turns.release.set()
        await wait_for_condition(
            lambda: all(
                status is DeliveryStatus.PROCESSED
                for status in self.peer.statuses.values()
            ),
            timeout=5,
        )


@asynccontextmanager
async def boundary_room(*, cap: int = 10) -> AsyncIterator[BoundaryRoom]:
    peer = BoundaryPeer()
    turns = Turns()
    async with peer.client() as rest:
        ctx = ExecutionContext(
            ROOM_ID,
            link_over(rest),
            turns.single,
            config=SessionConfig(
                consume_all_queued=True,
                max_queued_messages=cap,
                enable_context_hydration=False,
                enable_working_state=False,
            ),
            agent_id=AGENT_ID,
            on_execute_batch=turns.batch,
        )
        await ctx.start()
        try:
            # A public callback proves startup REST reconciliation has finished.
            await ctx.on_event(make_participant_added_event(room_id=ROOM_ID))
            await wait_for_condition(lambda: bool(turns.ids), timeout=5)
            turns.ids.clear()
            turns.gate = True
            await ctx.on_event(peer.post_at(WARMUP, 0))
            await wait_for_condition(lambda: turns.ids == [(WARMUP,)], timeout=5)
            yield BoundaryRoom(peer, ctx, turns)
        finally:
            turns.release.set()
            await ctx.stop(timeout=2)
