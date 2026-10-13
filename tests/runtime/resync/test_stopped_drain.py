"""A STOP that lands while a hub room drains its retained contact events."""

from __future__ import annotations

import asyncio

import pytest

from band.platform.event import MessageEvent
from band.runtime.execution import ExecutionContext, ResyncOutcome
from tests.conftest import make_message_event
from tests.runtime.executioninterrupt.kit import auxiliary_context
from tests.runtime.helpers import (
    LifecyclePlatform,
    contact_event,
    rest_client_over,
)

CONTACT_EVENTS = ["contact-1", "contact-2", "contact-3"]
NEIGHBOUR_ROOM = "room-neighbour"
NEIGHBOUR_MESSAGE = "neighbour-1"


class TestStopDuringSyntheticDrain:
    """The drain ends on STOP, the loop stays live, and PLAY resumes it."""

    # A drain that spins without yielding freezes the loop; fail rather than hang.
    @pytest.mark.timeout(5)
    async def test_stop_mid_drain_returns_and_play_handles_each_event_once(
        self,
    ) -> None:
        peer = LifecyclePlatform()
        handled: list[str] = []
        entered = asyncio.Event()

        async def hub_handler(ctx: ExecutionContext, event: MessageEvent) -> None:
            if not entered.is_set():
                entered.set()
                await asyncio.Future[None]()
            handled.append(event.payload.id)

        neighbour_handled: list[str] = []

        async def neighbour_handler(ctx: ExecutionContext, event: MessageEvent) -> None:
            neighbour_handled.append(event.payload.id)

        async with rest_client_over(peer.answer) as rest:
            hub = auxiliary_context(rest, hub_handler)
            neighbour = auxiliary_context(
                rest, neighbour_handler, room_id=NEIGHBOUR_ROOM
            )
            try:
                hub._pending_synthetic.extend(map(contact_event, CONTACT_EVENTS))
                drain = asyncio.create_task(hub._resync_pending_messages())
                await entered.wait()

                hub.stop_room()
                await neighbour._process_event(
                    make_message_event(room_id=NEIGHBOUR_ROOM, msg_id=NEIGHBOUR_MESSAGE)
                )

                assert await drain is ResyncOutcome.STOPPED
                assert neighbour_handled == [NEIGHBOUR_MESSAGE]
                assert handled == []

                await hub.resume_room()
                await hub._wait_until_resync_complete()
                assert handled == CONTACT_EVENTS
            finally:
                await hub.stop()
                await neighbour.stop()
