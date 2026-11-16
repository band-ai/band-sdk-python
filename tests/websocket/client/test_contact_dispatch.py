"""Each contact event on the agent's contacts channel reaches its own handler, typed."""

from __future__ import annotations

import asyncio
from functools import partial

import pytest
from band_sdk_core import AgentTopicKind

from band.client.streaming import WebSocketClient
from band.client.streaming.client import WireEvent, WirePayload
from band.testing import fake_phoenix_server
from tests.conftest import (
    make_contact_added_event,
    make_contact_removed_event,
    make_contact_request_received_event,
    make_contact_request_updated_event,
)
from tests.identifiers import AGENT_ID

CONTACT_TOPIC = AgentTopicKind.Contacts.topic(AGENT_ID)

Delivery = tuple[WireEvent, WirePayload]


class TestContactDispatch:
    @pytest.mark.parametrize(
        ("event", "payload"),
        [
            (
                WireEvent.CONTACT_REQUEST_RECEIVED,
                make_contact_request_received_event().payload,
            ),
            (
                WireEvent.CONTACT_REQUEST_UPDATED,
                make_contact_request_updated_event().payload,
            ),
            (WireEvent.CONTACT_ADDED, make_contact_added_event().payload),
            (WireEvent.CONTACT_REMOVED, make_contact_removed_event().payload),
        ],
        ids=lambda value: value if isinstance(value, WireEvent) else "",
    )
    async def test_pushed_contact_event_reaches_its_handler(
        self, event: WireEvent, payload: WirePayload
    ) -> None:
        deliveries: asyncio.Queue[Delivery] = asyncio.Queue()

        async def deliver(handled: WireEvent, received: WirePayload) -> None:
            await deliveries.put((handled, received))

        async with (
            fake_phoenix_server() as server,
            WebSocketClient(server.url, "test-key") as ws,
        ):
            await ws.join_agent_contacts_channel(
                AGENT_ID,
                on_contact_request_received=partial(
                    deliver, WireEvent.CONTACT_REQUEST_RECEIVED
                ),
                on_contact_request_updated=partial(
                    deliver, WireEvent.CONTACT_REQUEST_UPDATED
                ),
                on_contact_added=partial(deliver, WireEvent.CONTACT_ADDED),
                on_contact_removed=partial(deliver, WireEvent.CONTACT_REMOVED),
            )
            connection = await server.wait_for_connection(1)
            await connection.wait_for_join(CONTACT_TOPIC)

            await server.push(
                CONTACT_TOPIC, event, payload.model_dump(exclude_none=True)
            )

            assert await deliveries.get() == (event, payload)
