"""Shared PlatformRuntime test helpers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from tests.runtime.conftest import chat_row, make_link_mock


def serving_link(*room_ids: str) -> MagicMock:
    """A link whose presence admits ``room_ids`` and then delivers nothing."""
    link = make_link_mock()
    link.agent_id = "agent-123"
    link.is_connected = True
    link.subscribe_agent_rooms = AsyncMock()
    link.subscribe_room = AsyncMock()
    link.unsubscribe_room = AsyncMock()
    link.subscribe_agent_contacts = AsyncMock()
    link.unsubscribe_agent_contacts = AsyncMock()
    link.rest.agent_api_chats.list_agent_chats = AsyncMock(
        return_value=MagicMock(
            data=[chat_row(room_id) for room_id in room_ids],
            metadata=MagicMock(total_pages=1),
        )
    )

    async def no_events():
        await asyncio.Event().wait()
        yield

    link.__aiter__ = lambda _self: no_events()
    return link


ROOMS = ("room-1", "room-2")
