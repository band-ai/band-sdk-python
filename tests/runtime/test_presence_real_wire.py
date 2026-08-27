"""RoomPresence behavior proven against a real (fake-server-backed) wire, not
mocks calling each other -- mirrors tests/platform/test_link_real_wire.py one
layer up.

Every existing reconnect test in test_presence.py calls
``presence._handle_reconnect()`` directly, bypassing a real drop/reconnect
entirely. This file closes that gap: a real severed connection triggers
BandLink's own reconnect supervisor, which fires a real ``ReconnectedEvent``
through the real event queue, which RoomPresence's real event-consumer task
dispatches to ``_handle_reconnect`` -- the one seam a fully mocked suite
cannot exercise.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from band.platform.link import BandLink
from band.runtime.presence import RoomPresence
from band.testing import fake_phoenix_server


def make_link(server_url: str) -> BandLink:
    return BandLink(
        agent_id="agent-123",
        api_key="test-key",
        ws_url=server_url,
        rest_url="https://test.invalid",
    )


def chat_row(room_id: str) -> MagicMock:
    """One room as the chats listing returns it."""
    room = MagicMock()
    room.id = room_id
    room.model_dump.return_value = {"id": room_id}
    return room


def listing(*room_ids: str) -> AsyncMock:
    """Stub ``list_agent_chats`` returning one page naming ``room_ids``."""
    return AsyncMock(
        return_value=MagicMock(
            data=[chat_row(room_id) for room_id in room_ids],
            metadata=MagicMock(total_pages=1),
        )
    )


@asynccontextmanager
async def running_presence(link: BandLink):
    """Tear down every real resource (event-consumer task, room
    subscriptions, WS connection) even if an assertion in between raises.
    ``presence.stop()`` only reaches room-level state -- the connection is
    ``link``'s own, so it needs its own explicit close."""
    presence = RoomPresence(link)
    try:
        yield presence
    finally:
        await presence.stop()
        await link.disconnect()


async def test_reconnect_reconciles_room_membership_over_a_real_socket_drop() -> None:
    """A real severed connection must trigger BandLink's actual reconnect
    supervisor, which fires a real ReconnectedEvent through the real event
    queue -- not a direct ``_handle_reconnect()`` call -- and RoomPresence's
    reconciliation against the (drifted) REST snapshot must land correctly:
    a room dropped from the snapshot leaves, a newly-added one joins, and the
    true survivor gets resynced (not unioned with the newly-admitted room)."""
    async with fake_phoenix_server() as server:
        link = make_link(server.url)
        link.rest.agent_api_chats.list_agent_chats = listing("room-1", "room-2")

        async with running_presence(link) as presence:
            joined: list[str] = []
            left: list[str] = []
            resynced: list[str] = []
            reconnected = asyncio.Event()

            presence.on_room_joined = AsyncMock(
                side_effect=lambda room_id, payload: joined.append(room_id)
            )
            presence.on_room_left = AsyncMock(
                side_effect=lambda room_id: left.append(room_id)
            )

            async def on_event(room_id: str, event) -> None:
                resynced.append(room_id)

            presence.on_room_event = on_event

            async def on_reconnected() -> None:
                reconnected.set()

            presence.on_reconnected = on_reconnected

            await presence.start()
            assert set(presence.roster.tracked_room_ids()) == {"room-1", "room-2"}
            joined.clear()

            # Membership drifts while "disconnected": room-1 drops, room-3
            # appears, room-2 survives.
            link.rest.agent_api_chats.list_agent_chats = listing("room-2", "room-3")

            await server.abort_connection()
            await asyncio.wait_for(reconnected.wait(), timeout=10)

            assert left == ["room-1"]
            assert joined == ["room-3"]
            assert resynced == ["room-2"]
            assert set(presence.roster.tracked_room_ids()) == {"room-2", "room-3"}
