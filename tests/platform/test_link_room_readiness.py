"""BandLink asks a room to catch up whenever its subscription resumes after a gap."""

from __future__ import annotations

from band.platform.event import MessageEvent, ReconnectedEvent
from band.testing import fake_phoenix_server
from tests.platform.linkclose.kit import connected, make_link
from tests.platform.recovery.kit import ROOM_ID, ROOM_TOPIC
from tests.websocket.client.readiness.kit import (
    answer_join_then_push,
    crash_room_channel,
    push_message,
)


def outline(events: list) -> list:
    """Reconnects as themselves; messages by id."""
    return [
        event.payload.id if isinstance(event, MessageEvent) else event
        for event in events
    ]


class TestLinkRoomReadiness:
    async def test_initial_subscription_queues_no_catch_up(self) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            async with connected(link):
                await link.subscribe_room(ROOM_ID)
                await push_message(server, "m1")
                assert outline([await anext(link)]) == ["m1"]

    async def test_same_socket_recovery_queues_only_the_room_catch_up(self) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            async with connected(link):
                await link.subscribe_room(ROOM_ID)
                with server.defer_join(ROOM_TOPIC) as deferred:
                    await crash_room_channel(server)
                    await answer_join_then_push(server, deferred, "m1")
                events = [await anext(link), await anext(link)]

                assert outline(events) == [ReconnectedEvent(room_id=ROOM_ID), "m1"]
                assert server.connection_count == 1

    async def test_socket_reconnect_queues_the_room_catch_up_before_the_reconcile(
        self,
    ) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            async with connected(link):
                await link.subscribe_room(ROOM_ID)
                await server.abort_connection()
                events = [await anext(link), await anext(link)]

                assert events == [ReconnectedEvent(room_id=ROOM_ID), ReconnectedEvent()]
