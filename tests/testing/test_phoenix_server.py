"""Tests for the fake_phoenix_server testing utility itself.

Drives it through a real BandLink -- the actual consumer -- rather than
hand-crafting wire frames, so these tests double as proof the fake speaks a
protocol BandLink's real WebSocketClient/PHXChannelsClient stack accepts.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from band.platform.event import RoomAddedEvent
from band.platform.link import BandLink
from band.testing import JoinOutcome, fake_phoenix_server


def make_link(server_url: str) -> BandLink:
    return BandLink(
        agent_id="agent-123",
        api_key="test-key",
        ws_url=server_url,
        rest_url="https://test.invalid",
    )


async def wait_until(predicate, *, timeout: float = 5.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), timeout=timeout)


async def test_default_join_outcome_is_ok() -> None:
    """No declared policy -- every join succeeds, observed on both sides."""
    async with fake_phoenix_server() as server:
        link = make_link(server.url)
        await link.connect()

        await link.subscribe_room("room-1")

        assert link.is_room_subscribed("room-1") is True
        assert server.joined_topics >= {"chat_room:room-1", "room_participants:room-1"}


async def test_declared_join_outcome_rejects() -> None:
    """A REJECTED policy drives BandLink's real rollback path against a
    genuine phx_reply error, not a mocked exception."""
    async with fake_phoenix_server(
        join_outcomes={"chat_room:room-1": [JoinOutcome.REJECTED]}
    ) as server:
        link = make_link(server.url)
        await link.connect()

        await link.subscribe_room("room-1")

        assert link.is_room_subscribed("room-1") is False
        assert "chat_room:room-1" not in server.joined_topics


async def test_leave_is_acked() -> None:
    async with fake_phoenix_server() as server:
        link = make_link(server.url)
        await link.connect()
        await link.subscribe_room("room-1")

        await link.unsubscribe_room("room-1")

        assert link.is_room_subscribed("room-1") is False
        assert "chat_room:room-1" not in server.joined_topics
        assert "room_participants:room-1" not in server.joined_topics


async def test_push_delivers_inbound_event() -> None:
    """An unprompted server push reaches BandLink's real event queue over
    the real wire, not via a mocked handler call."""
    async with fake_phoenix_server() as server:
        link = make_link(server.url)
        await link.connect()
        await link.subscribe_agent_rooms("agent-123")

        await server.push(
            "agent_rooms:agent-123",
            "room_added",
            {
                "id": "room-9",
                "inserted_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            },
        )

        event = await asyncio.wait_for(link.__anext__(), timeout=5.0)
        assert isinstance(event, RoomAddedEvent)
        assert event.room_id == "room-9"


async def test_graceful_close_with_normal_code_does_not_reconnect() -> None:
    """Close code 1000 is what BandLink's reconnect policy treats as
    intentional -- the client must never open a second connection."""
    async with fake_phoenix_server() as server:
        link = make_link(server.url)
        await link.connect()
        assert server.connection_count == 1

        await server.close_connection(code=1000)
        await asyncio.sleep(0.3)

        assert server.connection_count == 1


async def test_abort_triggers_real_reconnect_and_drain() -> None:
    """A transport-level abort (no close handshake) is what a genuine
    network drop looks like -- the client's own reconnect logic must fire
    for real, and BandLink's reconciliation drain must run as part of it."""
    async with fake_phoenix_server() as server:
        link = make_link(server.url)
        await link.connect()
        await link.subscribe_room("room-1")
        drain_spy = AsyncMock(wraps=link._drain_reconciliation)
        link._drain_reconciliation = drain_spy

        await server.abort_connection()
        await wait_until(lambda: server.connection_count == 2)
        await wait_until(lambda: drain_spy.await_count > 0)

        assert link.is_room_subscribed("room-1") is True
