"""A refreshed token takes over through a new client, even from one that is
waiting to reconnect with the old token."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock

import pytest
from band_sdk_core import chat_room_topic
from phoenix_channels_python_client.authentication import AuthenticationHeader

from band.client.streaming import MessageCreatedPayload, WebSocketClient, WireEvent
from band.testing import fake_phoenix_server
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.kit import (
    VALID_MESSAGE_CREATED_PAYLOAD,
    credentials_sent,
    observe_retries,
)

EXPIRING_TOKEN = "synthetic-expiring-token"
REFRESHED_TOKEN = "synthetic-refreshed-token"
ROOM_ID = VALID_MESSAGE_CREATED_PAYLOAD["chat_room_id"]


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestCredentialReplacement:
    async def test_a_new_client_replaces_one_waiting_to_reconnect(self) -> None:
        delivered: asyncio.Queue[MessageCreatedPayload] = asyncio.Queue()
        topic = chat_room_topic(ROOM_ID)
        async with (
            controlled_clock() as clock,
            fake_phoenix_server() as server,
            AsyncExitStack() as old_session,
        ):
            old = await old_session.enter_async_context(
                WebSocketClient(server.url, access_token=EXPIRING_TOKEN)
            )
            await old.join_chat_room_channel(ROOM_ID, on_message_created=AsyncMock())
            with observe_retries() as retries:
                await server.abort_connection()
                old_retry_delay = await retries.get()

            async with WebSocketClient(server.url, access_token=REFRESHED_TOKEN) as new:
                await new.join_chat_room_channel(
                    ROOM_ID, on_message_created=delivered.put
                )
                await old_session.aclose()
                await clock.elapse(old_retry_delay)
                await server.push(
                    topic, WireEvent.MESSAGE_CREATED, VALID_MESSAGE_CREATED_PAYLOAD
                )
                received = await delivered.get()

                assert (old.joined_topics(), new.joined_topics()) == (
                    frozenset(),
                    frozenset({topic}),
                )

        assert received.id == VALID_MESSAGE_CREATED_PAYLOAD["id"]
        assert [credentials_sent(upgrade) for upgrade in server.upgrades] == [
            {AuthenticationHeader.TOKEN: EXPIRING_TOKEN},
            {AuthenticationHeader.TOKEN: REFRESHED_TOKEN},
        ]
