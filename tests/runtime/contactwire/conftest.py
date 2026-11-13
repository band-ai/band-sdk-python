"""A real REST client and link whose HTTP the test's ``httpx_mock`` answers."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from pytest_httpx import HTTPXMock

from band.client.rest import aclose_rest_client
from band.platform.link import BandLink
from band.runtime.contact_handler import ContactEventHandler
from band.runtime.contact_tools import ContactTools
from band.runtime.contacts import ContactEventConfig, ContactEventStrategy
from tests.identifiers import AGENT_ID
from tests.runtime.contactwire.kit import (
    AGENT_API_URL,
    HUB_ROOM_ID,
    REST_URL,
    STAMP,
    HubRoom,
)


@pytest_asyncio.fixture(loop_scope="function")
async def rest_link() -> AsyncIterator[BandLink]:
    """A link that is never connected: only its REST client is exercised."""
    link = BandLink(agent_id=AGENT_ID, api_key="test-key", rest_url=REST_URL)
    try:
        yield link
    finally:
        await aclose_rest_client(link.rest)


@pytest.fixture
def tools(rest_link: BandLink) -> ContactTools:
    return ContactTools(rest_link.rest)


@pytest.fixture
def hub_room() -> HubRoom:
    return HubRoom()


@pytest_asyncio.fixture(loop_scope="function")
async def hub_handler(
    rest_link: BandLink, hub_room: HubRoom, httpx_mock: HTTPXMock
) -> ContactEventHandler:
    """A HUB_ROOM handler whose hub room the platform created and is ready."""
    httpx_mock.add_response(
        method="POST",
        url=f"{AGENT_API_URL}/chats",
        json={
            "data": {
                "id": HUB_ROOM_ID,
                "title": "Contacts hub",
                "inserted_at": STAMP,
                "updated_at": STAMP,
            }
        },
    )
    handler = ContactEventHandler(
        ContactEventConfig(strategy=ContactEventStrategy.HUB_ROOM),
        rest_link,
        on_hub_event=hub_room.inject,
    )
    await handler.initialize_hub_room()
    handler.mark_hub_room_ready()
    return handler
