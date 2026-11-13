"""Each contact event on the agent's contacts channel reaches its own handler, typed."""

from __future__ import annotations

import asyncio
from functools import partial

import pytest
from band_sdk_core import AgentTopicKind

from band.client.streaming import (
    ContactAddedPayload,
    ContactRemovedPayload,
    ContactRequestReceivedPayload,
    ContactRequestUpdatedPayload,
    WebSocketClient,
)
from band.client.streaming.client import WireEvent, WirePayload
from band.testing import fake_phoenix_server
from tests.identifiers import AGENT_ID

CONTACT_TOPIC = AgentTopicKind.Contacts.topic(AGENT_ID)

Delivery = tuple[WireEvent, WirePayload]


class TestContactDispatch:
    @pytest.mark.parametrize(
        ("event", "payload"),
        [
            (
                WireEvent.CONTACT_REQUEST_RECEIVED,
                ContactRequestReceivedPayload(
                    id="request-1",
                    from_handle="jane_smith",
                    from_name="Jane Smith",
                    message="Let's work together",
                    status="pending",
                    inserted_at="2026-02-09T10:30:00Z",
                ),
            ),
            (
                WireEvent.CONTACT_REQUEST_UPDATED,
                ContactRequestUpdatedPayload(id="request-1", status="rejected"),
            ),
            (
                WireEvent.CONTACT_ADDED,
                ContactAddedPayload(
                    id="contact-1",
                    handle="jane_smith",
                    name="Jane Smith",
                    type="User",
                    inserted_at="2026-02-09T10:35:00Z",
                ),
            ),
            (WireEvent.CONTACT_REMOVED, ContactRemovedPayload(id="contact-1")),
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
