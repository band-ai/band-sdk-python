"""Slow rejoin keeps the native socket deadline rather than resetting it."""

from __future__ import annotations

import asyncio

import pytest

from band.client.streaming import MessageCreatedPayload, WebSocketClient
from band.client.streaming.client import WireEvent
from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import AGENT_ID, CONTROL_TOPIC
from tests.platform.recovery.kit import ROOM_ID, ROOM_TOPIC
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.heartbeat import ControlProbe, socket_of
from tests.websocket.client.kit import VALID_MESSAGE_CREATED_PAYLOAD
from tests.websocket.conftest import fast_session_policy


@pytest.mark.looptime(resolution=binary_clock_resolution())
@pytest.mark.usefixtures("millisecond_reconnects")
class TestHeartbeatRejoin:
    async def test_held_rejoin_renews_but_completion_grants_no_extra_grace(
        self,
    ) -> None:
        policy = fast_session_policy(heartbeat_interval_s=1, dead_threshold_s=3)
        probe = ControlProbe()
        messages: asyncio.Queue[MessageCreatedPayload] = asyncio.Queue()
        disconnected = asyncio.Event()
        reconnected = asyncio.Event()

        async def on_disconnect(error: Exception | None) -> None:
            disconnected.set()

        async def on_reconnect() -> None:
            reconnected.set()

        async with (
            controlled_clock() as clock,
            fake_phoenix_server() as server,
            WebSocketClient(
                server.url,
                "test-key",
                AGENT_ID,
                session_policy=policy,
                on_disconnect=on_disconnect,
                on_reconnect=on_reconnect,
            ) as client,
        ):
            await client.join_agent_control_channel(
                AGENT_ID,
                on_supersede=probe.unexpected_supersede,
                on_control=probe.receive,
            )
            await client.join_chat_room_channel(
                ROOM_ID, on_message_created=messages.put
            )
            with server.defer_join(ROOM_TOPIC) as deferred:
                await server.abort_connection()
                await disconnected.wait()
                await clock.elapse(0.01)
                await deferred.ready.wait()
                replacement = await server.wait_for_connection(2)
                assert deferred.connection is replacement
                await replacement.wait_for_join(CONTROL_TOPIC)
                for count in range(1, 5):
                    await clock.elapse(1)
                    await replacement.wait_for_heartbeats(count)
                    await probe.roundtrip(server)
                assert not reconnected.is_set()
                assert socket_of(client).close_code is None
                server.reply_heartbeats = False
                await clock.elapse(2)
                await replacement.wait_for_heartbeats(5)
                await probe.roundtrip(server)
                await server.release_join(deferred)
                await reconnected.wait()
                await server.push(
                    ROOM_TOPIC,
                    WireEvent.MESSAGE_CREATED,
                    VALID_MESSAGE_CREATED_PAYLOAD,
                )
                assert (await messages.get()).id == VALID_MESSAGE_CREATED_PAYLOAD["id"]
                await probe.roundtrip(server)
                assert socket_of(client).close_code is None
                await clock.elapse(1)
                await replacement.socket.wait_closed()
