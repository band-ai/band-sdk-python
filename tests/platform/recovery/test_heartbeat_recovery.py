"""Heartbeat failure restores real room/control delivery after reconciliation."""

from __future__ import annotations

import pytest

from band.client.streaming.client import WireEvent
from band.platform.event import MessageEvent, ReconnectedEvent
from band.testing import fake_phoenix_server, live_connection
from tests.platform.linkclose.kit import CONTROL_TOPIC, connected, make_link
from tests.platform.recovery.kit import (
    ROOM_ID,
    ROOM_TOPIC,
    ROOM_TOPICS,
    hold_reconciliation,
    observe_disconnects,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.heartbeat import ControlProbe
from tests.websocket.client.kit import VALID_MESSAGE_CREATED_PAYLOAD


@pytest.mark.looptime(resolution=binary_clock_resolution())
@pytest.mark.usefixtures("millisecond_reconnects")
class TestHeartbeatRecovery:
    async def test_timeout_rejoins_and_renews_while_reconciliation_is_held(
        self,
    ) -> None:
        probe = ControlProbe()
        async with (
            controlled_clock() as clock,
            fake_phoenix_server(reply_heartbeats=False) as server,
        ):
            link = make_link(server.url)
            disconnected = observe_disconnects(link)
            link.on_control = probe.receive
            async with connected(link), hold_reconciliation(link) as gate:
                await link.subscribe_room(ROOM_ID)
                old = await server.wait_for_connection(1)
                old_socket = live_connection(link)
                await clock.elapse(30)
                await old.wait_for_heartbeats(1)
                pong = await old_socket.ping()
                await pong
                await probe.roundtrip(server)
                await clock.elapse(30)
                await old.socket.wait_closed()
                await disconnected.get()
                server.reply_heartbeats = True
                await clock.elapse(0.01)
                replacement = await server.wait_for_connection(2)
                for topic in ROOM_TOPICS | {CONTROL_TOPIC}:
                    await replacement.wait_for_join(topic)
                await gate.entered.wait()
                async with background(anext(link)) as next_event:
                    for count in range(1, 4):
                        await clock.elapse(30)
                        await replacement.wait_for_heartbeats(count)
                        await probe.roundtrip(server)
                    assert live_connection(link).close_code is None
                    assert not next_event.done()
                    gate.released.set()
                    assert isinstance(await next_event, ReconnectedEvent)
                await server.push(
                    ROOM_TOPIC,
                    WireEvent.MESSAGE_CREATED,
                    VALID_MESSAGE_CREATED_PAYLOAD,
                )
                event = await anext(link)
                assert isinstance(event, MessageEvent)
                assert event.payload.id == VALID_MESSAGE_CREATED_PAYLOAD["id"]
                await probe.roundtrip(server)
                assert link.is_room_subscribed(ROOM_ID)
                assert old_socket.close_code is not None

    async def test_monitoring_expires_even_inside_reconciliation_callback(self) -> None:
        probe = ControlProbe()
        async with controlled_clock() as clock, fake_phoenix_server() as server:
            link = make_link(server.url)
            disconnected = observe_disconnects(link)
            link.on_control = probe.receive
            async with connected(link), hold_reconciliation(link) as gate:
                await server.abort_connection()
                await disconnected.get()
                await clock.elapse(0.01)
                replacement = await server.wait_for_connection(2)
                await gate.entered.wait()
                for count in range(1, 4):
                    await clock.elapse(30)
                    await replacement.wait_for_heartbeats(count)
                    await probe.roundtrip(server)
                server.reply_heartbeats = False
                await clock.elapse(59)
                await replacement.wait_for_heartbeats(4)
                await probe.roundtrip(server)
                assert replacement.socket.close_code is None
                await clock.elapse(1)
                await replacement.socket.wait_closed()
                assert replacement.socket.close_code is not None
                gate.released.set()
                await disconnected.get()
