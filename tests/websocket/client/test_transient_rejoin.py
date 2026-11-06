"""A timed-out rejoin stays retained until same-socket recovery succeeds."""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import MessageCreatedPayload, WebSocketClient
from band.client.streaming.client import WireEvent
from band.testing import fake_phoenix_server
from tests.platform.recovery.kit import ROOM_ID, ROOM_TOPIC
from tests.sessionpolicies import millisecond_backoff
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.kit import VALID_MESSAGE_CREATED_PAYLOAD


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestTransientRejoin:
    async def test_timeout_keeps_membership_and_later_recovers_delivery(self) -> None:
        reconnected = asyncio.Event()
        received: asyncio.Queue[MessageCreatedPayload] = asyncio.Queue()

        async def on_reconnect() -> None:
            reconnected.set()

        async with (
            controlled_clock() as clock,
            fake_phoenix_server() as server,
            WebSocketClient(
                server.url,
                "test-key",
                session_policy=millisecond_backoff(),
                on_reconnect=on_reconnect,
            ) as client,
        ):
            await client.join_chat_room_channel(ROOM_ID, received.put)
            assert client.client is not None
            client.client.join_timeout_s = 2
            with server.defer_join(ROOM_TOPIC) as deferred:
                await server.abort_connection()
                await clock.run_io(deferred.ready.wait())
                replacement = await server.wait_for_connection(2)
                await clock.elapse(client.client.join_timeout_s)
                await reconnected.wait()
                assert ROOM_TOPIC not in replacement.joined_topics
                assert client.joined_topics() == frozenset({ROOM_TOPIC})

            await clock.run_io(replacement.wait_for_join(ROOM_TOPIC))
            await server.push(
                ROOM_TOPIC, WireEvent.MESSAGE_CREATED, VALID_MESSAGE_CREATED_PAYLOAD
            )
            assert (await received.get()).id == VALID_MESSAGE_CREATED_PAYLOAD["id"]
            assert server.connection_count == 2
