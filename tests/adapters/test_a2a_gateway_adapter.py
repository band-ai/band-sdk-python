"""Unit tests for A2A Gateway context_id persistence (mock-based).

Tests the internal context mapping logic without hitting the real platform.
"""

from __future__ import annotations

import asyncio
from typing import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.types import Message as A2AMessage, Part, Role, TextPart

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
        room_1, ctx_1 = await adapter._resolve_room("ctx-user-session", "uuid-weather")

        # Second request with SAME context_id
        room_2, ctx_2 = await adapter._resolve_room("ctx-user-session", "uuid-weather")

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
        room_a, ctx_a = await adapter._resolve_room("ctx-session-a", "uuid-weather")

        # Second context (different)
        room_b, ctx_b = await adapter._resolve_room("ctx-session-b", "uuid-weather")

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
        room_1, _ = await adapter._resolve_room("ctx-multi", "uuid-weather")

        # Second peer, same context
        room_2, _ = await adapter._resolve_room("ctx-multi", "uuid-data")

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
    to post the message. A warm turn, where the peer is already a participant,
    adds no settle of its own, though it can still wait behind an earlier
    concurrent turn in the same context.
    """

    @pytest.mark.asyncio
    async def test_fresh_join_waits_for_settle(
        self, make_gateway_adapter: Callable[..., A2AGatewayAdapter]
    ) -> None:
        """A fresh join must not return until the settle window completes.

        The settle is what gives a freshly-added peer time to subscribe before
        the first message is posted. Gating the settle on an event verifies the
        wait deterministically: room resolution stays pending until the settle
        is released, then completes.
        """
        adapter = make_gateway_adapter()

        entered_settle = asyncio.Event()
        release_settle = asyncio.Event()

        async def gated_settle() -> None:
            entered_settle.set()
            await release_settle.wait()

        adapter._settle_new_participant = gated_settle

        task = asyncio.create_task(adapter._resolve_room("ctx-fresh", "uuid-weather"))
        await asyncio.wait_for(entered_settle.wait(), timeout=1.0)

        assert not task.done(), "resolve returned before the settle completed"

        release_settle.set()
        _, context_id = await asyncio.wait_for(task, timeout=1.0)
        assert context_id == "ctx-fresh"

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
            adapter._resolve_room("ctx-warm", "uuid-weather"), timeout=1.0
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
            adapter._resolve_room("ctx-fast", "uuid-weather"), timeout=1.0
        )

        adapter._rest.agent_api_participants.add_agent_chat_participant.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrent_turns_same_context_each_get_own_reply(
        self, make_gateway_adapter: Callable[..., A2AGatewayAdapter]
    ) -> None:
        """Overlapping turns in one context serialize; both callers get a reply.

        Band correlates a peer's reply only by room, so the gateway holds one
        in-flight turn per room: the second turn does not post until the first
        has streamed its terminal event. Regression guard for the old bug where
        both turns registered a pending task on the same room, the second
        overwrote the first, and the first caller's stream hung forever.
        """
        adapter = make_gateway_adapter(settle_seconds=0.0)

        # Posting a message makes the mentioned peer reply into that room.
        async def reply_after_post(**kwargs: object) -> MagicMock:
            room = kwargs["chat_id"]

            async def deliver() -> None:
                # Let the turn register its pending task and start awaiting.
                await asyncio.sleep(0)
                reply = MagicMock()
                reply.content = "answer"
                reply.message_type = "text"
                await adapter.on_message(
                    reply,
                    MagicMock(),
                    None,
                    None,
                    None,
                    is_session_bootstrap=False,
                    room_id=room,
                )

            asyncio.get_running_loop().create_task(deliver())
            return MagicMock()

        adapter._rest.agent_api_messages.create_agent_chat_message = AsyncMock(
            side_effect=reply_after_post
        )

        async def drive(text: str) -> list[object]:
            message = A2AMessage(
                role=Role.user,
                message_id=text,
                parts=[Part(root=TextPart(text=text))],
                context_id="ctx-shared",
            )
            return [
                event
                async for event in adapter._handle_a2a_request("weather-agent", message)
            ]

        events_a, events_b = await asyncio.wait_for(
            asyncio.gather(drive("first"), drive("second")), timeout=2.0
        )

        # Both callers received their own terminal event; neither hung.
        assert events_a and events_a[-1].final
        assert events_b and events_b[-1].final
        # The peer was joined once and the room created once.
        adapter._rest.agent_api_participants.add_agent_chat_participant.assert_called_once()
        assert len(adapter._rooms_created) == 1
        assert (
            adapter._rest.agent_api_messages.create_agent_chat_message.call_count == 2
        )
        # Each turn removed its own pending entry on completion.
        assert adapter._pending_tasks == {}

    @pytest.mark.asyncio
    async def test_join_recorded_before_settle_survives_cancellation(
        self, make_gateway_adapter: Callable[..., A2AGatewayAdapter]
    ) -> None:
        """State recorded before the settle survives a turn cancelled mid-settle.

        The context to room mapping and participant membership are recorded the
        instant the REST calls succeed, before the settle. So a cancel during
        the settle strands neither an orphan room nor a lost join: a later turn
        reuses the room and does not re-add the peer.
        """
        adapter = make_gateway_adapter()

        entered_settle = asyncio.Event()
        release_settle = asyncio.Event()

        async def gated_settle() -> None:
            entered_settle.set()
            await release_settle.wait()

        adapter._settle_new_participant = gated_settle

        task = asyncio.create_task(adapter._resolve_room("ctx", "uuid-weather"))
        await asyncio.wait_for(entered_settle.wait(), timeout=1.0)

        # Room creation + REST add have completed; state must already reflect them.
        room_id = adapter._context_to_room["ctx"]
        assert adapter._rooms_created == [room_id]
        assert adapter._room_participants[room_id] == {"uuid-weather"}

        # Cancel the turn while it is paused in the settle.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Recorded state persists across the cancellation.
        assert adapter._context_to_room["ctx"] == room_id
        assert adapter._room_participants[room_id] == {"uuid-weather"}

        # A later turn reuses the room and does not re-add the (warm) peer.
        add_mock = adapter._rest.agent_api_participants.add_agent_chat_participant
        calls_before = add_mock.call_count
        resolved_room, resolved_ctx = await adapter._resolve_room("ctx", "uuid-weather")
        assert (resolved_room, resolved_ctx) == (room_id, "ctx")
        assert add_mock.call_count == calls_before
