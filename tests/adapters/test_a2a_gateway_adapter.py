"""Unit tests for A2A Gateway context_id persistence (mock-based).

Tests the internal context mapping logic without hitting the real platform.
"""

from __future__ import annotations

import asyncio
from typing import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.integrations.a2a.gateway import A2AGatewayAdapter
from band_rest import Peer


@pytest.fixture
def make_gateway_adapter() -> Callable[..., A2AGatewayAdapter]:
    """Factory for a gateway adapter with mocked REST calls.

    Returns a builder so each test picks its own settle window. The builder
    stubs every REST call the room-resolution path touches, registers a
    "weather" peer, and exposes ``_rooms_created`` (the ids handed out by
    ``create_agent_chat``, in order) for assertions.
    """

    def build(settle_seconds: float = 0.0) -> A2AGatewayAdapter:
        adapter = A2AGatewayAdapter(
            rest_url="http://localhost:4000",
            api_key="test-key",
            new_participant_settle_seconds=settle_seconds,
        )

        weather_peer = Peer(
            id="uuid-weather",
            name="Weather Agent",
            type="Agent",
            handle="test/weather-agent",
            is_contact=False,
            source="registry",
        )
        adapter._peers = {"weather-agent": weather_peer}
        adapter._peers_by_uuid = {"uuid-weather": weather_peer}

        rooms_created: list[str] = []

        def track_room_creation(*args: object, **kwargs: object) -> MagicMock:
            room_id = f"room-{len(rooms_created) + 1}"
            rooms_created.append(room_id)
            response = MagicMock()
            response.data = MagicMock(id=room_id)
            return response

        adapter._rest.agent_api_chats.create_agent_chat = AsyncMock(
            side_effect=track_room_creation
        )
        adapter._rest.agent_api_participants.add_agent_chat_participant = AsyncMock()
        adapter._rest.agent_api_messages.create_agent_chat_message = AsyncMock()
        adapter._rest.agent_api_events.create_agent_chat_event = AsyncMock()
        adapter._rooms_created = rooms_created  # Expose for assertions

        return adapter

    return build


class TestA2AGatewayContextIdFlow:
    """Unit tests for context_id persistence in A2A Gateway (mock-based)."""

    @pytest.mark.asyncio
    async def test_same_context_id_twice_uses_same_room(
        self, make_gateway_adapter: Callable[..., A2AGatewayAdapter]
    ) -> None:
        """Same contextId sent twice should reuse the same chat room."""
        adapter = make_gateway_adapter()

        # First request with context_id="ctx-user-session"
        room_1, ctx_1 = await adapter._get_or_create_room(
            "ctx-user-session", "uuid-weather"
        )

        # Second request with SAME context_id
        room_2, ctx_2 = await adapter._get_or_create_room(
            "ctx-user-session", "uuid-weather"
        )

        # Assertions
        assert room_1 == room_2, f"Expected same room, got {room_1} vs {room_2}"
        assert ctx_1 == ctx_2 == "ctx-user-session"
        assert len(adapter._rooms_created) == 1, "Should only create 1 room"
        assert adapter._rest.agent_api_chats.create_agent_chat.call_count == 1

    @pytest.mark.asyncio
    async def test_different_context_ids_create_different_rooms(
        self, make_gateway_adapter: Callable[..., A2AGatewayAdapter]
    ) -> None:
        """Different contextIds should create separate chat rooms."""
        adapter = make_gateway_adapter()

        # First context
        room_a, ctx_a = await adapter._get_or_create_room(
            "ctx-session-a", "uuid-weather"
        )

        # Second context (different)
        room_b, ctx_b = await adapter._get_or_create_room(
            "ctx-session-b", "uuid-weather"
        )

        # Assertions
        assert room_a != room_b, f"Expected different rooms, got same: {room_a}"
        assert ctx_a == "ctx-session-a"
        assert ctx_b == "ctx-session-b"
        assert len(adapter._rooms_created) == 2, "Should create 2 separate rooms"

    @pytest.mark.asyncio
    async def test_same_context_different_peers_same_room_adds_peer(
        self, make_gateway_adapter: Callable[..., A2AGatewayAdapter]
    ) -> None:
        """Same contextId with different peers should use same room, add peer."""
        adapter = make_gateway_adapter()

        # Add second peer
        data_peer = Peer(
            id="uuid-data",
            name="Data Agent",
            type="Agent",
            handle="test/data-agent",
            is_contact=False,
            source="registry",
        )
        adapter._peers["data-agent"] = data_peer
        adapter._peers_by_uuid["uuid-data"] = data_peer

        # First peer
        room_1, _ = await adapter._get_or_create_room("ctx-multi", "uuid-weather")

        # Second peer, same context
        room_2, _ = await adapter._get_or_create_room("ctx-multi", "uuid-data")

        # Assertions
        assert room_1 == room_2, "Same context should use same room"
        assert len(adapter._rooms_created) == 1, "Should only create 1 room"
        assert "uuid-weather" in adapter._room_participants[room_1]
        assert "uuid-data" in adapter._room_participants[room_1]
        assert (
            adapter._rest.agent_api_participants.add_agent_chat_participant.call_count
            == 2
        )


class TestFreshlyJoinedPeerSettle:
    """The gateway must let a freshly-added peer subscribe before the first message.

    A peer added to a room needs a moment for its execution context to
    subscribe to the room's real-time feed. If the gateway posts the first
    message during that window, the peer can discover the message through both
    its catch-up poll and its live feed and answer it more than once. The
    gateway therefore settles after adding a peer and before returning control
    to post the message. Warm rooms, where the peer is already a participant,
    never add a peer and so never wait.
    """

    @pytest.mark.asyncio
    async def test_first_message_waits_until_fresh_peer_subscribes(
        self, make_gateway_adapter: Callable[..., A2AGatewayAdapter]
    ) -> None:
        """A fresh join must not return until the peer has had time to subscribe.

        Reproduction: the peer subscribes a short while after being added. With
        no settle window the gateway returns immediately, while the peer is
        still unsubscribed, which is the race that lets the first message be
        answered multiple times. The settle window must outlast the peer's
        subscription so the gateway only proceeds once the peer is ready.
        """
        # Peer's subscription completes shortly after it is added to the room.
        peer_subscribed = asyncio.Event()

        async def start_peer_subscription(*args: object, **kwargs: object) -> None:
            async def subscribe() -> None:
                await asyncio.sleep(0.05)
                peer_subscribed.set()

            asyncio.get_running_loop().create_task(subscribe())

        # Settle window comfortably longer than the peer's subscribe time.
        adapter = make_gateway_adapter(settle_seconds=0.5)
        adapter._rest.agent_api_participants.add_agent_chat_participant = AsyncMock(
            side_effect=start_peer_subscription
        )

        await adapter._get_or_create_room("ctx-fresh", "uuid-weather")

        assert peer_subscribed.is_set(), (
            "gateway returned before the freshly-added peer finished subscribing; "
            "the first message would be posted into the duplicate-processing window"
        )

    @pytest.mark.asyncio
    async def test_warm_room_reuse_never_waits(
        self, make_gateway_adapter: Callable[..., A2AGatewayAdapter]
    ) -> None:
        """Reusing a room where the peer is already a member must not settle."""
        # A settle this long would hang the test if the warm path waited.
        adapter = make_gateway_adapter(settle_seconds=30.0)
        adapter._context_to_room["ctx-warm"] = "room-existing"
        adapter._room_participants["room-existing"] = {"uuid-weather"}

        room_id, _ = await asyncio.wait_for(
            adapter._get_or_create_room("ctx-warm", "uuid-weather"), timeout=1.0
        )

        assert room_id == "room-existing"
        adapter._rest.agent_api_participants.add_agent_chat_participant.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_settle_returns_without_waiting(
        self, make_gateway_adapter: Callable[..., A2AGatewayAdapter]
    ) -> None:
        """A zero settle window opts out of the pause entirely."""
        # A peer that never signals readiness: proves the gateway does not wait.
        adapter = make_gateway_adapter(settle_seconds=0.0)

        await asyncio.wait_for(
            adapter._get_or_create_room("ctx-fast", "uuid-weather"), timeout=1.0
        )

        adapter._rest.agent_api_participants.add_agent_chat_participant.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrent_requests_same_context_join_peer_once(
        self, make_gateway_adapter: Callable[..., A2AGatewayAdapter]
    ) -> None:
        """Concurrent requests in one conversation must join the peer only once.

        While the first request sits in the settle window, its peer join is not
        yet recorded. A second concurrent request for the same context and peer
        must wait for that join to finish rather than issue a duplicate add or
        post before the settle ends.
        """
        adapter = make_gateway_adapter(settle_seconds=0.1)
        # Existing conversation whose room has no participants recorded yet.
        adapter._context_to_room["ctx"] = "room-1"
        adapter._room_participants["room-1"] = set()

        results = await asyncio.gather(
            adapter._get_or_create_room("ctx", "uuid-weather"),
            adapter._get_or_create_room("ctx", "uuid-weather"),
        )

        assert results == [("room-1", "ctx"), ("room-1", "ctx")]
        adapter._rest.agent_api_participants.add_agent_chat_participant.assert_called_once()
        assert adapter._room_participants["room-1"] == {"uuid-weather"}
