"""BandLink queues one catch-up per gap: the reconnect's, or the recovered topic's."""

from __future__ import annotations

from band_sdk_core import AgentTopicKind
from websockets.frames import CloseCode

from band.platform.event import (
    MessageEvent,
    ReconnectedEvent,
    WebSocketDisconnectedEvent,
)
from band.testing import fake_phoenix_server
from tests.conftest import CLOSED_1008
from tests.platform.linkclose.kit import AGENT_ID, connected, make_link
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

    async def test_socket_reconnect_queues_only_its_reconcile(self) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            async with connected(link):
                await link.subscribe_agent_rooms(AGENT_ID)
                await link.subscribe_room(ROOM_ID)
                await server.abort_connection()
                assert await anext(link) == ReconnectedEvent()

                await push_message(server, "m1")
                assert outline([await anext(link)]) == ["m1"]

    async def test_recovery_after_a_socket_reconnect_catches_up_again(self) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            async with connected(link):
                await link.subscribe_room(ROOM_ID)
                await server.abort_connection()
                assert await anext(link) == ReconnectedEvent()

                await crash_room_channel(server)
                assert await anext(link) == ReconnectedEvent(room_id=ROOM_ID)

    async def test_agent_rooms_recovery_on_a_live_socket_queues_a_reconcile(
        self,
    ) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            async with connected(link):
                await link.subscribe_agent_rooms(AGENT_ID)
                await server.crash_channel(AgentTopicKind.Rooms.topic(AGENT_ID))

                assert await anext(link) == ReconnectedEvent()
                assert server.connection_count == 1

    async def test_a_new_session_after_a_terminal_close_catches_up_again(
        self,
    ) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            async with connected(link):
                await server.close_connection(code=CloseCode.POLICY_VIOLATION)
                assert await anext(link) == WebSocketDisconnectedEvent(
                    payload=CLOSED_1008
                )

            async with connected(link):
                await link.subscribe_room(ROOM_ID)
                await crash_room_channel(server)
                assert await anext(link) == ReconnectedEvent(room_id=ROOM_ID)
