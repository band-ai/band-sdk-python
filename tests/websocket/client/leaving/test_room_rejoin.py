"""Server-closed room topics do not prevent a later room admission."""

from __future__ import annotations

import pytest
from phoenix_channels_python_client.phx_messages import PHXEvent

from band.client.streaming.client import WireEvent
from band.platform.event import MessageEvent
from band.testing import fake_phoenix_server
from tests.websocket.client.kit import VALID_MESSAGE_CREATED_PAYLOAD
from tests.websocket.client.leaving.kit import (
    PARTICIPANTS_TOPIC,
    ROOM_ID,
    ROOM_TOPIC,
    make_link,
    observe_topic_loss,
)


class TestRoomRejoin:
    @pytest.mark.parametrize("closed_topic", [ROOM_TOPIC, PARTICIPANTS_TOPIC])
    async def test_server_closed_topic_allows_same_connection_rejoin(
        self, closed_topic: str
    ) -> None:
        async with fake_phoenix_server() as server:
            link = make_link(server.url)
            try:
                await link.connect()
                await link.subscribe_room(ROOM_ID)
                with observe_topic_loss() as loss:
                    await server.push(closed_topic, PHXEvent.close.value, {})
                    await loss.completed.wait()

                await link.unsubscribe_room(ROOM_ID)
                await link.subscribe_room(ROOM_ID)
                assert link.is_room_subscribed(ROOM_ID)

                await server.push(
                    ROOM_TOPIC, WireEvent.MESSAGE_CREATED, VALID_MESSAGE_CREATED_PAYLOAD
                )
                event = await anext(link)
                assert isinstance(event, MessageEvent)
                assert event.payload is not None
                assert event.payload.id == VALID_MESSAGE_CREATED_PAYLOAD["id"]
                assert server.connection_count == 1
            finally:
                await link.disconnect()
