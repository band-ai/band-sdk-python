"""Tests for A2AGatewayAdapter."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from a2a.types import (
    Message as A2AMessage,
    Part,
    Role,
    TaskState,
    TextPart,
)

from band.core.types import PlatformMessage
from band.integrations.a2a.gateway import (
    A2AGatewayAdapter,
    GatewaySessionState,
)
from band.testing import FakeAgentTools
from band_rest import Peer
from band_rest.core.api_error import ApiError


def make_platform_message(
    content: str, room_id: str = "room-123", message_type: str = "text"
) -> PlatformMessage:
    """Create a test PlatformMessage."""
    return PlatformMessage(
        id=str(uuid4()),
        room_id=room_id,
        content=content,
        sender_id="peer-456",
        sender_type="Agent",
        sender_name="Weather Agent",
        message_type=message_type,
        metadata={},
        created_at=datetime.now(),
    )


def make_peer(peer_id: str, name: str, description: str = "") -> Peer:
    """Create a mock Peer object."""
    return Peer(
        id=peer_id,
        name=name,
        type="Agent",
        description=description,
        handle=f"test/{name.lower().replace(' ', '-')}",
        is_contact=False,
        source="registry",
    )


def make_a2a_message(
    content: str, context_id: str | None = None, task_id: str | None = None
) -> A2AMessage:
    """Create an A2A message for testing."""
    return A2AMessage(
        role=Role.user,
        message_id=str(uuid4()),
        parts=[Part(root=TextPart(text=content))],
        context_id=context_id,
        task_id=task_id,
    )


class TestA2AGatewayAdapterInit:
    """Tests for A2AGatewayAdapter initialization."""

    def test_init_default_values(self) -> None:
        """Should initialize with default values."""
        adapter = A2AGatewayAdapter()

        assert adapter.gateway_url == "http://localhost:10000"
        assert adapter.port == 10000
        assert adapter._peers == {}
        assert adapter._server is None
        assert adapter._context_to_room == {}
        assert adapter._room_participants == {}
        assert adapter._pending_tasks == {}

    def test_init_with_custom_values(self) -> None:
        """Should accept custom configuration."""
        adapter = A2AGatewayAdapter(
            rest_url="https://custom.api.com",
            api_key="test-key",
            gateway_url="http://localhost:9000",
            port=9000,
        )

        assert adapter.gateway_url == "http://localhost:9000"
        assert adapter.port == 9000

    def test_init_creates_rest_client(self) -> None:
        """Should create AsyncRestClient."""
        adapter = A2AGatewayAdapter(
            rest_url="https://api.example.com",
            api_key="my-key",
        )

        assert adapter._rest is not None

    def test_init_sets_history_converter(self) -> None:
        """Should set GatewayHistoryConverter."""
        adapter = A2AGatewayAdapter()

        assert adapter.history_converter is not None


class TestA2AGatewayAdapterOnStarted:
    """Tests for A2AGatewayAdapter.on_started()."""

    @pytest.mark.asyncio
    async def test_on_started_fetches_peers_via_rest(self) -> None:
        """Should fetch peers from REST API."""
        adapter = A2AGatewayAdapter()

        # Mock REST client
        mock_response = MagicMock()
        mock_response.data = [
            make_peer("weather", "Weather Agent"),
            make_peer("servicenow", "ServiceNow Agent"),
        ]
        adapter._rest.agent_api_peers.list_agent_peers = AsyncMock(
            return_value=mock_response
        )

        # Mock server
        with patch(
            "band.integrations.a2a.gateway.adapter.GatewayServer"
        ) as mock_server_class:
            mock_server = MagicMock()
            mock_server.start = AsyncMock()
            mock_server_class.return_value = mock_server

            await adapter.on_started("Gateway", "A2A Gateway Agent")

        # Peers are now keyed by slug, not UUID
        assert len(adapter._peers) == 2
        assert "weather-agent" in adapter._peers
        assert "servicenow-agent" in adapter._peers
        # UUID fallback should also be populated
        assert "weather" in adapter._peers_by_uuid
        assert "servicenow" in adapter._peers_by_uuid

    @pytest.mark.asyncio
    async def test_on_started_starts_http_server(self) -> None:
        """Should start HTTP server with peer routes."""
        adapter = A2AGatewayAdapter(port=10001)

        # Mock REST client
        mock_response = MagicMock()
        mock_response.data = [make_peer("weather", "Weather Agent")]
        adapter._rest.agent_api_peers.list_agent_peers = AsyncMock(
            return_value=mock_response
        )

        # Mock server
        with patch(
            "band.integrations.a2a.gateway.adapter.GatewayServer"
        ) as mock_server_class:
            mock_server = MagicMock()
            mock_server.start = AsyncMock()
            mock_server_class.return_value = mock_server

            await adapter.on_started("Gateway", "A2A Gateway Agent")

            mock_server_class.assert_called_once()
            mock_server.start.assert_called_once()
            assert adapter._server is mock_server

    @pytest.mark.asyncio
    async def test_on_started_stores_agent_info(self) -> None:
        """Should store agent name and description."""
        adapter = A2AGatewayAdapter()

        # Mock REST client
        mock_response = MagicMock()
        mock_response.data = []
        adapter._rest.agent_api_peers.list_agent_peers = AsyncMock(
            return_value=mock_response
        )

        # Mock server
        with patch(
            "band.integrations.a2a.gateway.adapter.GatewayServer"
        ) as mock_server_class:
            mock_server = MagicMock()
            mock_server.start = AsyncMock()
            mock_server_class.return_value = mock_server

            await adapter.on_started("Test Gateway", "A test gateway")

        assert adapter.agent_name == "Test Gateway"
        assert adapter.agent_description == "A test gateway"

    @pytest.mark.asyncio
    async def test_on_started_retries_peer_discovery_on_rate_limit(self) -> None:
        """Should retry peer discovery when startup hits HTTP 429."""
        adapter = A2AGatewayAdapter()

        mock_response = MagicMock()
        mock_response.data = [make_peer("weather", "Weather Agent")]
        adapter._rest.agent_api_peers.list_agent_peers = AsyncMock(
            side_effect=[
                ApiError(status_code=429, headers={}, body=""),
                ApiError(status_code=429, headers={}, body=""),
                mock_response,
            ]
        )

        with patch(
            "band.integrations.a2a.gateway.adapter.GatewayServer"
        ) as mock_server_class:
            mock_server = MagicMock()
            mock_server.start = AsyncMock()
            mock_server_class.return_value = mock_server
            with patch(
                "band.integrations.a2a.gateway.adapter.asyncio.sleep",
                new=AsyncMock(),
            ) as mock_sleep:
                await adapter.on_started("Gateway", "A2A Gateway Agent")

        assert adapter._rest.agent_api_peers.list_agent_peers.await_count == 3
        assert mock_sleep.await_count == 2


class TestA2AGatewayAdapterOnMessage:
    """Tests for A2AGatewayAdapter.on_message()."""

    @pytest.fixture
    def adapter_with_mocks(self) -> A2AGatewayAdapter:
        """Create adapter with mocked dependencies."""
        adapter = A2AGatewayAdapter()
        adapter._peers = {"weather": make_peer("weather", "Weather Agent")}
        adapter._rest.agent_api_chats.create_agent_chat = AsyncMock()
        adapter._rest.agent_api_participants.add_agent_chat_participant = AsyncMock()
        adapter._rest.agent_api_messages.create_agent_chat_message = AsyncMock()
        adapter._rest.agent_api_events.create_agent_chat_event = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_on_message_rehydrates_on_bootstrap(
        self, adapter_with_mocks: A2AGatewayAdapter
    ) -> None:
        """Should rehydrate session state on bootstrap."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello", room_id="room-123")

        history = GatewaySessionState(
            context_to_room={"ctx-1": "room-1", "ctx-2": "room-2"},
            room_participants={"room-1": {"peer-a"}, "room-2": {"peer-b"}},
        )

        await adapter_with_mocks.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        assert adapter_with_mocks._context_to_room == {
            "ctx-1": "room-1",
            "ctx-2": "room-2",
        }
        assert adapter_with_mocks._room_participants == {
            "room-1": {"peer-a"},
            "room-2": {"peer-b"},
        }

    @pytest.mark.asyncio
    async def test_on_message_correlates_pending_task(
        self, adapter_with_mocks: A2AGatewayAdapter
    ) -> None:
        """Should push event to pending task's SSE queue."""
        tools = FakeAgentTools()
        msg = make_platform_message("Weather is sunny", room_id="room-123")

        # Set up pending task
        from band.integrations.a2a.gateway.types import PendingA2ATask
        from a2a.types import Task, TaskStatus

        sse_queue: asyncio.Queue = asyncio.Queue()
        task = Task(
            id="task-123",
            context_id="ctx-123",
            status=TaskStatus(state=TaskState.working),
        )
        adapter_with_mocks._pending_tasks["room-123"] = PendingA2ATask(
            task=task,
            sse_queue=sse_queue,
            peer_id="weather",
        )

        await adapter_with_mocks.on_message(
            msg,
            tools,
            GatewaySessionState(),
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        # Event should be in queue
        assert not sse_queue.empty()
        event = sse_queue.get_nowait()
        assert event.task_id == "task-123"
        assert event.final is True  # text message = completed


class TestA2AGatewayAdapterRoomManagement:
    """Tests for room creation and management."""

    @pytest.fixture
    def adapter_with_mocks(self) -> A2AGatewayAdapter:
        """Create adapter with mocked REST client."""
        adapter = A2AGatewayAdapter(new_participant_settle_seconds=0.0)
        adapter._peers = {"weather": make_peer("weather", "Weather Agent")}

        # Mock create_agent_chat to return room with ID
        mock_chat_response = MagicMock()
        mock_chat_response.data = MagicMock()
        mock_chat_response.data.id = "new-room-123"
        adapter._rest.agent_api_chats.create_agent_chat = AsyncMock(
            return_value=mock_chat_response
        )
        adapter._rest.agent_api_participants.add_agent_chat_participant = AsyncMock()
        return adapter

    @pytest.mark.asyncio
    async def test_resolve_room_new_context(
        self, adapter_with_mocks: A2AGatewayAdapter
    ) -> None:
        """Should create new room for new context."""
        room_id, context_id = await adapter_with_mocks._resolve_room(None, "weather")

        assert room_id == "new-room-123"
        assert context_id is not None  # UUID generated
        assert adapter_with_mocks._context_to_room[context_id] == room_id
        assert "weather" in adapter_with_mocks._room_participants[room_id]

        # REST calls should be made
        adapter_with_mocks._rest.agent_api_chats.create_agent_chat.assert_called_once()
        adapter_with_mocks._rest.agent_api_participants.add_agent_chat_participant.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_room_existing_context(
        self, adapter_with_mocks: A2AGatewayAdapter
    ) -> None:
        """Should reuse existing room for known context."""
        # Pre-populate context mapping
        adapter_with_mocks._context_to_room["existing-ctx"] = "existing-room"
        adapter_with_mocks._room_participants["existing-room"] = {"weather"}

        room_id, context_id = await adapter_with_mocks._resolve_room(
            "existing-ctx", "weather"
        )

        assert room_id == "existing-room"
        assert context_id == "existing-ctx"

        # No new room should be created
        adapter_with_mocks._rest.agent_api_chats.create_agent_chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_room_adds_new_participant(
        self, adapter_with_mocks: A2AGatewayAdapter
    ) -> None:
        """Should add new participant to existing room."""
        # Pre-populate context mapping with different peer
        adapter_with_mocks._context_to_room["ctx-1"] = "room-1"
        adapter_with_mocks._room_participants["room-1"] = {"other-peer"}

        room_id, context_id = await adapter_with_mocks._resolve_room("ctx-1", "weather")

        assert room_id == "room-1"
        assert "weather" in adapter_with_mocks._room_participants["room-1"]

        # Should add participant but not create room
        adapter_with_mocks._rest.agent_api_chats.create_agent_chat.assert_not_called()
        adapter_with_mocks._rest.agent_api_participants.add_agent_chat_participant.assert_called_once()


class TestA2AGatewayAdapterRehydration:
    """Tests for session rehydration."""

    def test_rehydrate_restores_context_mapping(self) -> None:
        """Should restore context → room mappings."""
        adapter = A2AGatewayAdapter()

        history = GatewaySessionState(
            context_to_room={"ctx-1": "room-1", "ctx-2": "room-2"},
            room_participants={},
        )

        adapter._rehydrate(history)

        assert adapter._context_to_room == {"ctx-1": "room-1", "ctx-2": "room-2"}

    def test_rehydrate_restores_participants(self) -> None:
        """Should restore room participants."""
        adapter = A2AGatewayAdapter()

        history = GatewaySessionState(
            context_to_room={},
            room_participants={"room-1": {"peer-a", "peer-b"}},
        )

        adapter._rehydrate(history)

        assert adapter._room_participants["room-1"] == {"peer-a", "peer-b"}

    def test_rehydrate_merges_with_existing(self) -> None:
        """Should merge with existing state, not replace."""
        adapter = A2AGatewayAdapter()
        adapter._context_to_room["existing-ctx"] = "existing-room"
        adapter._room_participants["existing-room"] = {"existing-peer"}

        history = GatewaySessionState(
            context_to_room={"new-ctx": "new-room"},
            room_participants={"new-room": {"new-peer"}},
        )

        adapter._rehydrate(history)

        # Both old and new should be present
        assert adapter._context_to_room["existing-ctx"] == "existing-room"
        assert adapter._context_to_room["new-ctx"] == "new-room"

    def test_rehydrate_does_not_overwrite_existing_context(self) -> None:
        """Should not overwrite existing context mappings."""
        adapter = A2AGatewayAdapter()
        adapter._context_to_room["ctx-1"] = "current-room"

        history = GatewaySessionState(
            context_to_room={"ctx-1": "old-room"},  # Same context, different room
            room_participants={},
        )

        adapter._rehydrate(history)

        # Should keep current mapping
        assert adapter._context_to_room["ctx-1"] == "current-room"


class TestA2AGatewayAdapterTranslation:
    """Tests for message translation."""

    def test_translate_to_a2a_text_message(self) -> None:
        """Should translate text message to completed event."""
        adapter = A2AGatewayAdapter()
        msg = make_platform_message("Hello world")

        from a2a.types import Task, TaskStatus

        task = Task(
            id="task-123",
            context_id="ctx-123",
            status=TaskStatus(state=TaskState.working),
        )

        event = adapter._translate_to_a2a(msg, task)

        assert event.task_id == "task-123"
        assert event.context_id == "ctx-123"
        assert event.status.state == TaskState.completed
        assert event.final is True

    def test_translate_to_a2a_thought_message(self) -> None:
        """Should translate thought message to working event."""
        adapter = A2AGatewayAdapter()
        msg = make_platform_message("Thinking...", message_type="thought")

        from a2a.types import Task, TaskStatus

        task = Task(
            id="task-123",
            context_id="ctx-123",
            status=TaskStatus(state=TaskState.working),
        )

        event = adapter._translate_to_a2a(msg, task)

        assert event.status.state == TaskState.working
        assert event.final is False

    def test_translate_to_a2a_error_message(self) -> None:
        """Should translate error message to failed event."""
        adapter = A2AGatewayAdapter()
        msg = make_platform_message("Something went wrong", message_type="error")

        from a2a.types import Task, TaskStatus

        task = Task(
            id="task-123",
            context_id="ctx-123",
            status=TaskStatus(state=TaskState.working),
        )

        event = adapter._translate_to_a2a(msg, task)

        assert event.status.state == TaskState.failed
        assert event.final is True


class TestA2AGatewayAdapterCleanup:
    """Tests for cleanup methods."""

    @pytest.mark.asyncio
    async def test_on_cleanup_removes_pending_task(self) -> None:
        """Should remove pending task for room."""
        adapter = A2AGatewayAdapter()

        from band.integrations.a2a.gateway.types import PendingA2ATask
        from a2a.types import Task, TaskStatus

        sse_queue: asyncio.Queue = asyncio.Queue()
        task = Task(
            id="task-123",
            context_id="ctx-123",
            status=TaskStatus(state=TaskState.working),
        )
        adapter._pending_tasks["room-123"] = PendingA2ATask(
            task=task,
            sse_queue=sse_queue,
            peer_id="weather",
        )

        await adapter.on_cleanup("room-123")

        assert "room-123" not in adapter._pending_tasks

    @pytest.mark.asyncio
    async def test_stop_stops_server(self) -> None:
        """Should stop HTTP server."""
        adapter = A2AGatewayAdapter()
        mock_server = MagicMock()
        mock_server.stop = AsyncMock()
        adapter._server = mock_server

        await adapter.stop()

        mock_server.stop.assert_called_once()
        assert adapter._server is None


class TestGatewayContextIdRoomMapping:
    """Tests that gateway maps context_id to rooms correctly."""

    @pytest.fixture
    def adapter_with_tracking(self) -> A2AGatewayAdapter:
        """Create adapter with mocked REST client that tracks room creation."""
        adapter = A2AGatewayAdapter(new_participant_settle_seconds=0.0)
        adapter._peers = {"weather": make_peer("weather", "Weather Agent")}

        # Track room creation with unique IDs
        rooms_created: list[str] = []

        def create_room_side_effect(*args, **kwargs):
            room_id = f"room-{len(rooms_created) + 1}"
            rooms_created.append(room_id)
            mock_response = MagicMock()
            mock_response.data = MagicMock()
            mock_response.data.id = room_id
            return mock_response

        adapter._rest.agent_api_chats.create_agent_chat = AsyncMock(
            side_effect=create_room_side_effect
        )
        adapter._rest.agent_api_participants.add_agent_chat_participant = AsyncMock()
        adapter._rooms_created = rooms_created  # Expose for assertions
        return adapter

    @pytest.mark.asyncio
    async def test_same_context_id_twice_reuses_room(
        self, adapter_with_tracking: A2AGatewayAdapter
    ) -> None:
        """Same context_id should map to same room - no new room created."""
        adapter = adapter_with_tracking

        # First request with context_id
        room_id_1, ctx_1 = await adapter._resolve_room("ctx-user-123", "weather")
        create_calls_after_first = (
            adapter._rest.agent_api_chats.create_agent_chat.call_count
        )

        # Second request with SAME context_id
        room_id_2, ctx_2 = await adapter._resolve_room("ctx-user-123", "weather")
        create_calls_after_second = (
            adapter._rest.agent_api_chats.create_agent_chat.call_count
        )

        # Same room, same context
        assert room_id_1 == room_id_2
        assert ctx_1 == ctx_2 == "ctx-user-123"
        # Only created once, not twice
        assert create_calls_after_first == 1
        assert create_calls_after_second == 1

    @pytest.mark.asyncio
    async def test_different_context_ids_create_different_rooms(
        self, adapter_with_tracking: A2AGatewayAdapter
    ) -> None:
        """Different context_ids should create different rooms."""
        adapter = adapter_with_tracking

        room_id_1, ctx_1 = await adapter._resolve_room("ctx-first", "weather")
        room_id_2, ctx_2 = await adapter._resolve_room("ctx-second", "weather")

        # Different rooms for different contexts
        assert room_id_1 != room_id_2
        assert ctx_1 == "ctx-first"
        assert ctx_2 == "ctx-second"
        # Created twice
        assert adapter._rest.agent_api_chats.create_agent_chat.call_count == 2

    @pytest.mark.asyncio
    async def test_same_context_different_peers_same_room(
        self, adapter_with_tracking: A2AGatewayAdapter
    ) -> None:
        """Same context_id with different peers should use same room, add peers."""
        adapter = adapter_with_tracking
        adapter._peers["data"] = make_peer("data", "Data Agent")

        room_id_1, _ = await adapter._resolve_room("ctx-multi-agent", "weather")
        room_id_2, _ = await adapter._resolve_room("ctx-multi-agent", "data")

        # Same room
        assert room_id_1 == room_id_2
        # Both peers added to room
        assert "weather" in adapter._room_participants[room_id_1]
        assert "data" in adapter._room_participants[room_id_1]
        # Room created once, but participant added twice
        assert adapter._rest.agent_api_chats.create_agent_chat.call_count == 1
        assert (
            adapter._rest.agent_api_participants.add_agent_chat_participant.call_count
            == 2
        )
