"""A running Agent holds the contacts channel over the real wire and delivers its events."""

from __future__ import annotations

import asyncio

import pytest
from band_sdk_core import AgentTopicKind
from phoenix_channels_python_client import SystemEvent

from band.client.streaming import ContactAddedPayload, WireEvent
from band.platform.event import ContactAddedEvent, ContactEvent
from band.runtime.contact_tools import ContactTools
from band.runtime.contacts import ContactEventConfig, ContactEventStrategy
from band.testing import fake_phoenix_server
from tests.agentcontacts.kit import contact_agent
from tests.identifiers import AGENT_ID

CONTACT_TOPIC = AgentTopicKind.Contacts.topic(AGENT_ID)
CONTACT = ContactAddedPayload(
    id="contact-1",
    handle="jane_smith",
    name="Jane Smith",
    type="User",
    inserted_at="2026-02-09T10:35:00Z",
)


async def ignore(event: ContactEvent, tools: ContactTools) -> None:
    return None


@pytest.mark.usefixtures("platform_rest")
class TestContactChannelWire:
    @pytest.mark.parametrize(
        "contact_config",
        [
            ContactEventConfig(strategy=ContactEventStrategy.CALLBACK, on_event=ignore),
            ContactEventConfig(
                strategy=ContactEventStrategy.DISABLED, broadcast_changes=True
            ),
        ],
        ids=["callback", "disabled-with-broadcast"],
    )
    async def test_agent_joins_contacts_while_running_and_leaves_on_stop(
        self, contact_config: ContactEventConfig
    ) -> None:
        async with fake_phoenix_server() as server:
            async with contact_agent(server.url, contact_config):
                connection = await server.wait_for_connection(1)
                await connection.wait_for_join(CONTACT_TOPIC)

            assert CONTACT_TOPIC not in connection.joined_topics

    async def test_agent_without_contact_handling_never_joins_contacts(self) -> None:
        """A join sent during start would reach the server before start returned."""
        async with (
            fake_phoenix_server() as server,
            contact_agent(server.url, ContactEventConfig()),
        ):
            joined = {m.topic for m in server.received if m.event == SystemEvent.join}

        assert CONTACT_TOPIC not in joined

    async def test_callback_receives_a_contact_event_the_platform_pushes(
        self,
    ) -> None:
        received: asyncio.Queue[ContactEvent] = asyncio.Queue()

        async def on_event(event: ContactEvent, tools: ContactTools) -> None:
            await received.put(event)

        callback = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK, on_event=on_event
        )
        async with (
            fake_phoenix_server() as server,
            contact_agent(server.url, callback),
        ):
            connection = await server.wait_for_connection(1)
            await connection.wait_for_join(CONTACT_TOPIC)

            await server.push(
                CONTACT_TOPIC,
                WireEvent.CONTACT_ADDED,
                CONTACT.model_dump(exclude_none=True),
            )

            assert await received.get() == ContactAddedEvent(payload=CONTACT)
