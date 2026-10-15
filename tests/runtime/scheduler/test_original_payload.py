"""Observable discovery and dispatch contracts over the real REST client."""

from __future__ import annotations

from typing import Any

import pytest

from band.client.streaming import DeliveryStatus, MessageMetadata
from band.platform.event import MessageEvent
from band.runtime.execution import ExecutionContext
from tests.conftest import make_message_event
from tests.platform.settlement.kit import link_over
from tests.runtime.conftest import wait_for_condition
from tests.runtime.delivery.kit import RestPeer, session_config
from tests.runtime.helpers import AGENT_ID, ROOM_ID, rest_client_over

pytestmark = [pytest.mark.asyncio, pytest.mark.looptime]


class TestOriginalPayload:
    @pytest.mark.parametrize("batching", [False])
    async def test_preserve_original(self, batching: bool) -> None:
        peer = RestPeer()
        received: list[Any] = []

        async def execute(ctx: ExecutionContext, event: MessageEvent) -> None:
            received.append(event.payload)

        async def batch(
            ctx: ExecutionContext, events: tuple[MessageEvent, ...]
        ) -> None:
            received.extend(event.payload for event in events)

        async with rest_client_over(peer.answer) as rest:
            ctx = ExecutionContext(
                ROOM_ID,
                link_over(rest),
                execute,
                config=session_config(batching=batching),
                agent_id=AGENT_ID,
                on_execute_batch=batch,
            )
            await ctx.start()
            try:
                # Finish the initial empty scan before introducing a real arrival.
                await wait_for_condition(
                    lambda: bool(peer.reads if batching else peer.nexts)
                )
                event = peer.post("original")
                # The socket mentions carry usernames; REST stores display names.
                event = make_message_event(
                    room_id=ROOM_ID,
                    msg_id="original",
                    content="original",
                    sender_id="user-1",
                    metadata=MessageMetadata(
                        status="sent",
                        mentions=[{"id": AGENT_ID, "username": "agent-handle"}],
                    ),
                )
                expected = event.payload.metadata.model_dump()
                peer.refuse_processing.add("original")
                await ctx.on_event(event)
                await wait_for_condition(
                    lambda: bool(peer.writes_of(DeliveryStatus.PROCESSING))
                )
                peer.refuse_processing.clear()
                await ctx.request_resync()
                await wait_for_condition(
                    lambda: peer.status_of("original") is DeliveryStatus.PROCESSED
                )
            finally:
                await ctx.stop(timeout=2)
        assert len(received) == 1
        assert received[0].metadata.model_dump() == expected
        assert event.payload.metadata.model_dump() == expected
