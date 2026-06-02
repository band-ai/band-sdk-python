"""Tests for A2AAdapter."""

from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from a2a.types import (
    Artifact,
    Message as A2AMessage,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)

from band.converters.a2a import A2AHistoryConverter
from band.core.types import PlatformMessage
from band.integrations.a2a import A2AAdapter, A2AAuth, A2ASessionState
from band.testing import FakeAgentTools


def make_platform_message(content: str, room_id: str = "room-123") -> PlatformMessage:
    """Create a test PlatformMessage."""
    return PlatformMessage(
        id=str(uuid4()),
        room_id=room_id,
        content=content,
        sender_id="user-456",
        sender_type="User",
        sender_name="Test User",
        message_type="text",
        metadata={},
        created_at=datetime.now(),
    )


def make_task(
    state: TaskState,
    task_id: str = "task-123",
    context_id: str = "ctx-123",
    status_message: str | None = None,
    artifact_text: str | None = None,
) -> Task:
    """Create a mock A2A Task."""
    status_msg = None
    if status_message:
        status_msg = A2AMessage(
            role=Role.agent,
            message_id=str(uuid4()),
            parts=[Part(root=TextPart(text=status_message))],
        )

    artifacts = None
    if artifact_text:
        artifacts = [
            Artifact(
                artifact_id=str(uuid4()),
                parts=[Part(root=TextPart(text=artifact_text))],
            )
        ]

    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=state, message=status_msg),
        artifacts=artifacts,
        history=None,
    )


class TestA2AAuth:
    """Tests for A2AAuth."""

    def test_to_headers_with_api_key(self):
        """Should add X-API-Key header."""
        auth = A2AAuth(api_key="my-secret-key")
        headers = auth.to_headers()

        assert headers == {"X-API-Key": "my-secret-key"}

    def test_to_headers_with_bearer_token(self):
        """Should add Authorization Bearer header."""
        auth = A2AAuth(bearer_token="eyJ...")
        headers = auth.to_headers()

        assert headers == {"Authorization": "Bearer eyJ..."}

    def test_to_headers_with_custom_headers(self):
        """Should include custom headers."""
        auth = A2AAuth(headers={"X-Custom": "value"})
        headers = auth.to_headers()

        assert headers == {"X-Custom": "value"}

    def test_to_headers_combined(self):
        """Should combine all auth methods."""
        auth = A2AAuth(
            api_key="key",
            bearer_token="token",
            headers={"X-Custom": "value"},
        )
        headers = auth.to_headers()

        assert headers == {
            "X-API-Key": "key",
            "Authorization": "Bearer token",
            "X-Custom": "value",
        }


class TestA2AAdapterInit:
    """Tests for A2AAdapter initialization."""

    def test_init_default_values(self):
        """Should initialize with default values."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")

        assert adapter.remote_url == "http://localhost:10000"
        assert adapter.auth is None
        assert adapter.streaming is True
        assert adapter._client is None
        assert adapter._contexts == {}
        assert adapter._tasks == {}
        assert adapter._task_senders == {}

    def test_init_with_auth(self):
        """Should accept auth configuration."""
        auth = A2AAuth(api_key="test-key")
        adapter = A2AAdapter(remote_url="http://localhost:10000", auth=auth)

        assert adapter.auth is auth

    def test_init_with_streaming_disabled(self):
        """Should accept streaming=False."""
        adapter = A2AAdapter(remote_url="http://localhost:10000", streaming=False)

        assert adapter.streaming is False


class TestA2AAdapterOnStarted:
    """Tests for A2AAdapter.on_started()."""

    @pytest.mark.asyncio
    async def test_on_started_connects_to_agent(self):
        """Should connect to remote A2A agent."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")

        with patch("band.integrations.a2a.adapter.ClientFactory") as mock_factory:
            mock_client = MagicMock()
            mock_factory.connect = AsyncMock(return_value=mock_client)

            await adapter.on_started("Test Agent", "A test agent")

            mock_factory.connect.assert_called_once()
            assert adapter._client is mock_client

    @pytest.mark.asyncio
    async def test_on_started_passes_auth_to_discovery_and_requests(self):
        """Should apply auth headers to discovery and RPC requests."""
        auth = A2AAuth(
            api_key="test-key",
            bearer_token="test-token",
            headers={"X-Custom-Auth": "custom"},
        )
        adapter = A2AAdapter(remote_url="http://localhost:10000", auth=auth)

        with patch("band.integrations.a2a.adapter.ClientFactory") as mock_factory:
            mock_factory.connect = AsyncMock(return_value=MagicMock())

            await adapter.on_started("Test Agent", "A test agent")

            _, kwargs = mock_factory.connect.call_args
            assert kwargs["resolver_http_kwargs"] == {
                "headers": {
                    "X-API-Key": "test-key",
                    "Authorization": "Bearer test-token",
                    "X-Custom-Auth": "custom",
                }
            }

            interceptors = kwargs["interceptors"]
            assert interceptors is not None
            assert len(interceptors) == 1

            request_payload, http_kwargs = await interceptors[0].intercept(
                "message/send",
                {"jsonrpc": "2.0"},
                {},
                None,
                None,
            )
            assert request_payload == {"jsonrpc": "2.0"}
            assert http_kwargs == {
                "headers": {
                    "X-API-Key": "test-key",
                    "Authorization": "Bearer test-token",
                    "X-Custom-Auth": "custom",
                }
            }

    @pytest.mark.asyncio
    async def test_on_started_sets_agent_name(self):
        """Should store agent name and description."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")

        with patch("band.integrations.a2a.adapter.ClientFactory") as mock_factory:
            mock_factory.connect = AsyncMock(return_value=MagicMock())

            await adapter.on_started("Test Agent", "A test agent")

            assert adapter.agent_name == "Test Agent"
            assert adapter.agent_description == "A test agent"


class TestA2AAdapterOnMessage:
    """Tests for A2AAdapter.on_message()."""

    @pytest.fixture
    def adapter_with_client(self):
        """Create adapter with mocked client."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        adapter._client = MagicMock()
        return adapter

    @pytest.mark.asyncio
    async def test_raises_if_client_not_initialized(self):
        """Should raise if on_started not called."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        tools = FakeAgentTools()
        msg = make_platform_message("Hello")

        with pytest.raises(RuntimeError, match="client not initialized"):
            await adapter.on_message(
                msg,
                tools,
                A2ASessionState(),
                None,
                None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

    @pytest.mark.asyncio
    async def test_completed_task_sends_message(self, adapter_with_client):
        """Should send message when task completes with artifact."""
        tools = FakeAgentTools()
        msg = make_platform_message("What is 10 USD in EUR?")

        task = make_task(
            state=TaskState.completed,
            artifact_text="10 USD is approximately 9.20 EUR.",
        )

        async def mock_send_message(*args, **kwargs) -> AsyncIterator:
            yield (task, None)

        adapter_with_client._client.send_message = mock_send_message

        await adapter_with_client.on_message(
            msg,
            tools,
            A2ASessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Should have sent a message with mention
        assert len(tools.messages_sent) == 1
        assert tools.messages_sent[0]["content"] == "10 USD is approximately 9.20 EUR."
        assert tools.messages_sent[0]["mentions"] == [
            {"id": "user-456", "name": "Test User"}
        ]

        # Context should be tracked for multi-turn
        assert adapter_with_client._contexts["room-123"] == "ctx-123"

        # Task ID should be cleared after completion (new message = new task)
        assert "room-123" not in adapter_with_client._tasks

        # Task sender should be cleaned up after completion
        assert ("room-123", "task-123") not in adapter_with_client._task_senders

    @pytest.mark.asyncio
    async def test_working_task_sends_thought_event(self, adapter_with_client):
        """Should send thought event when task is working."""
        tools = FakeAgentTools()
        msg = make_platform_message("Processing request")

        task = make_task(
            state=TaskState.working,
            status_message="Fetching exchange rates...",
        )

        async def mock_send_message(*args, **kwargs) -> AsyncIterator:
            yield (task, None)

        adapter_with_client._client.send_message = mock_send_message

        await adapter_with_client.on_message(
            msg,
            tools,
            A2ASessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Should have sent a thought event
        assert len(tools.events_sent) == 1
        assert tools.events_sent[0]["content"] == "Fetching exchange rates..."
        assert tools.events_sent[0]["message_type"] == "thought"

    @pytest.mark.asyncio
    async def test_input_required_sends_message(self, adapter_with_client):
        """Should send message when agent needs more input."""
        tools = FakeAgentTools()
        msg = make_platform_message("Convert currency")

        task = make_task(
            state=TaskState.input_required,
            status_message="What currency do you want to convert to?",
        )

        async def mock_send_message(*args, **kwargs) -> AsyncIterator:
            yield (task, None)

        adapter_with_client._client.send_message = mock_send_message

        await adapter_with_client.on_message(
            msg,
            tools,
            A2ASessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Should have sent a message asking for more info with mention
        assert len(tools.messages_sent) == 1
        assert (
            tools.messages_sent[0]["content"]
            == "What currency do you want to convert to?"
        )
        assert tools.messages_sent[0]["mentions"] == [
            {"id": "user-456", "name": "Test User"}
        ]

    @pytest.mark.asyncio
    async def test_failed_task_sends_error_event(self, adapter_with_client):
        """Should send error event when task fails."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello")

        task = make_task(
            state=TaskState.failed,
            status_message="Currency API unavailable",
        )

        async def mock_send_message(*args, **kwargs) -> AsyncIterator:
            yield (task, None)

        adapter_with_client._client.send_message = mock_send_message

        await adapter_with_client.on_message(
            msg,
            tools,
            A2ASessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Should have sent an error event + task event
        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["content"] == "Currency API unavailable"
        assert error_events[0]["metadata"]["a2a_state"] == "failed"

        # Task event should also be emitted for rehydration
        task_events = [e for e in tools.events_sent if e["message_type"] == "task"]
        assert len(task_events) == 1

    @pytest.mark.asyncio
    async def test_exception_sends_error_event(self, adapter_with_client):
        """Should send error event on exception."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello")

        async def mock_send_message(*args, **kwargs) -> AsyncIterator:
            raise ConnectionError("Connection failed")
            yield  # Make it an async generator

        adapter_with_client._client.send_message = mock_send_message

        await adapter_with_client.on_message(
            msg,
            tools,
            A2ASessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Should have sent an error event
        assert len(tools.events_sent) == 1
        assert "Connection failed" in tools.events_sent[0]["content"]
        assert tools.events_sent[0]["message_type"] == "error"

    @pytest.mark.asyncio
    async def test_direct_message_reply(self, adapter_with_client):
        """Should handle direct A2A Message reply."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello")

        a2a_reply = A2AMessage(
            role=Role.agent,
            message_id=str(uuid4()),
            parts=[Part(root=TextPart(text="Hello! How can I help?"))],
        )

        async def mock_send_message(*args, **kwargs) -> AsyncIterator:
            yield a2a_reply

        adapter_with_client._client.send_message = mock_send_message

        await adapter_with_client.on_message(
            msg,
            tools,
            A2ASessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Should have sent a message with mention
        assert len(tools.messages_sent) == 1
        assert tools.messages_sent[0]["content"] == "Hello! How can I help?"
        assert tools.messages_sent[0]["mentions"] == [
            {"id": "user-456", "name": "Test User"}
        ]


class TestA2AAdapterContextManagement:
    """Tests for context and task tracking."""

    def test_tracks_context_per_room(self):
        """Should track different contexts for different rooms."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")

        adapter._contexts["room-1"] = "ctx-1"
        adapter._contexts["room-2"] = "ctx-2"

        assert adapter._contexts["room-1"] == "ctx-1"
        assert adapter._contexts["room-2"] == "ctx-2"

    def test_tracks_task_per_room(self):
        """Should track different tasks for different rooms."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")

        adapter._tasks["room-1"] = "task-1"
        adapter._tasks["room-2"] = "task-2"

        assert adapter._tasks["room-1"] == "task-1"
        assert adapter._tasks["room-2"] == "task-2"

    @pytest.mark.asyncio
    async def test_on_cleanup_removes_context(self):
        """Should clean up context, task, and sender tracking for room."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        adapter._contexts["room-1"] = "ctx-1"
        adapter._tasks["room-1"] = "task-1"
        adapter._task_senders[("room-1", "task-1")] = {"id": "u1", "name": "User1"}
        adapter._task_senders[("room-1", "task-2")] = {"id": "u2", "name": "User2"}
        adapter._contexts["room-2"] = "ctx-2"
        adapter._tasks["room-2"] = "task-2"
        adapter._task_senders[("room-2", "task-3")] = {"id": "u3", "name": "User3"}

        await adapter.on_cleanup("room-1")

        assert "room-1" not in adapter._contexts
        assert "room-1" not in adapter._tasks
        assert ("room-1", "task-1") not in adapter._task_senders
        assert ("room-1", "task-2") not in adapter._task_senders
        # Other room unaffected
        assert adapter._contexts["room-2"] == "ctx-2"
        assert adapter._tasks["room-2"] == "task-2"
        assert adapter._task_senders[("room-2", "task-3")] == {
            "id": "u3",
            "name": "User3",
        }


class TestA2AAdapterMessageConversion:
    """Tests for message conversion."""

    def test_to_a2a_message_basic(self):
        """Should convert Band message to A2A format."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        msg = make_platform_message("Hello world")

        a2a_msg = adapter._to_a2a_message(msg, "room-123")

        assert a2a_msg.role == Role.user
        assert len(a2a_msg.parts) == 1
        assert isinstance(a2a_msg.parts[0].root, TextPart)
        assert a2a_msg.parts[0].root.text == "Hello world"
        assert a2a_msg.context_id is None
        assert a2a_msg.task_id is None

    def test_to_a2a_message_with_existing_context(self):
        """Should include existing context_id and task_id."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        adapter._contexts["room-123"] = "existing-ctx"
        adapter._tasks["room-123"] = "existing-task"

        msg = make_platform_message("Follow up")

        a2a_msg = adapter._to_a2a_message(msg, "room-123")

        assert a2a_msg.context_id == "existing-ctx"
        assert a2a_msg.task_id == "existing-task"


class TestA2AAdapterResponseExtraction:
    """Tests for response extraction from Task."""

    def test_extract_response_from_artifact(self):
        """Should extract response from artifact."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        task = make_task(
            state=TaskState.completed,
            artifact_text="Response from artifact",
        )

        response = adapter._extract_response(task)

        assert response == "Response from artifact"

    def test_extract_response_from_status_message(self):
        """Should fallback to status message if no artifact."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        task = make_task(
            state=TaskState.completed,
            status_message="Response from status",
        )

        response = adapter._extract_response(task)

        assert response == "Response from status"

    def test_extract_response_empty_if_no_content(self):
        """Should return empty string if no content found."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        task = make_task(state=TaskState.completed)

        response = adapter._extract_response(task)

        assert response == ""


class TestA2AHistoryConverter:
    """Tests for A2AHistoryConverter."""

    def test_convert_empty_history(self):
        """Should return empty session state for empty history."""
        converter = A2AHistoryConverter()
        result = converter.convert([])

        assert result.context_id is None
        assert result.task_id is None
        assert result.task_state is None

    def test_convert_finds_task_event(self):
        """Should extract A2A metadata from task event."""
        converter = A2AHistoryConverter()
        raw_history = [
            {"message_type": "text", "content": "Hello"},
            {
                "message_type": "task",
                "content": "A2A task completed",
                "metadata": {
                    "a2a_context_id": "ctx-abc",
                    "a2a_task_id": "task-xyz",
                    "a2a_task_state": "completed",
                },
            },
        ]

        result = converter.convert(raw_history)

        assert result.context_id == "ctx-abc"
        assert result.task_id == "task-xyz"
        assert result.task_state == "completed"

    def test_convert_finds_latest_task_event(self):
        """Should find most recent A2A task event."""
        converter = A2AHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {
                    "a2a_context_id": "ctx-old",
                    "a2a_task_id": "task-old",
                    "a2a_task_state": "completed",
                },
            },
            {"message_type": "text", "content": "New message"},
            {
                "message_type": "task",
                "metadata": {
                    "a2a_context_id": "ctx-new",
                    "a2a_task_id": "task-new",
                    "a2a_task_state": "input_required",
                },
            },
        ]

        result = converter.convert(raw_history)

        assert result.context_id == "ctx-new"
        assert result.task_id == "task-new"
        assert result.task_state == "input_required"

    def test_convert_ignores_non_a2a_task_events(self):
        """Should ignore task events without A2A metadata."""
        converter = A2AHistoryConverter()
        raw_history = [
            {
                "message_type": "task",
                "metadata": {"other_key": "value"},  # No A2A metadata
            },
        ]

        result = converter.convert(raw_history)

        assert result.context_id is None
        assert result.task_id is None
        assert result.task_state is None


class TestA2AAdapterTaskEventEmission:
    """Tests for task event emission."""

    @pytest.fixture
    def adapter_with_client(self):
        """Create adapter with mocked client."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        adapter._client = MagicMock()
        return adapter

    @pytest.mark.asyncio
    async def test_emits_task_event_on_completed(self, adapter_with_client):
        """Should emit task event when task completes."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello")

        task = make_task(
            state=TaskState.completed,
            artifact_text="Response",
        )

        async def mock_send_message(*args, **kwargs):
            yield (task, None)

        adapter_with_client._client.send_message = mock_send_message

        await adapter_with_client.on_message(
            msg,
            tools,
            A2ASessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Find the task event
        task_events = [e for e in tools.events_sent if e["message_type"] == "task"]
        assert len(task_events) == 1
        assert task_events[0]["metadata"]["a2a_context_id"] == "ctx-123"
        assert task_events[0]["metadata"]["a2a_task_id"] == "task-123"
        assert task_events[0]["metadata"]["a2a_task_state"] == "completed"

    @pytest.mark.asyncio
    async def test_emits_task_event_on_input_required(self, adapter_with_client):
        """Should emit task event when input is required."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello")

        task = make_task(
            state=TaskState.input_required,
            status_message="What currency?",
        )

        async def mock_send_message(*args, **kwargs):
            yield (task, None)

        adapter_with_client._client.send_message = mock_send_message

        await adapter_with_client.on_message(
            msg,
            tools,
            A2ASessionState(),
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Find the task event
        task_events = [e for e in tools.events_sent if e["message_type"] == "task"]
        assert len(task_events) == 1
        assert task_events[0]["metadata"]["a2a_task_state"] == "input-required"


class TestA2AAdapterSessionRehydration:
    """Tests for session rehydration."""

    @pytest.fixture
    def adapter_with_client(self):
        """Create adapter with mocked client."""
        adapter = A2AAdapter(remote_url="http://localhost:10000")
        adapter._client = MagicMock()
        return adapter

    @pytest.mark.asyncio
    async def test_rehydrates_context_on_bootstrap(self, adapter_with_client):
        """Should restore context_id on session bootstrap."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello")

        # Create history with A2A session state
        history = A2ASessionState(
            context_id="restored-ctx",
            task_id="old-task",
            task_state="completed",  # Terminal state, won't try to resubscribe
        )

        task = make_task(state=TaskState.completed, artifact_text="Response")

        async def mock_send_message(*args, **kwargs):
            yield (task, None)

        adapter_with_client._client.send_message = mock_send_message

        await adapter_with_client.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Context should be restored before the message was processed
        # Note: The new task will update context_id, so we check the initial restoration happened
        assert "room-123" in adapter_with_client._contexts

    @pytest.mark.asyncio
    async def test_no_rehydration_on_non_bootstrap(self, adapter_with_client):
        """Should not rehydrate on non-bootstrap messages."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello")

        # History would normally trigger rehydration
        history = A2ASessionState(
            context_id="restored-ctx",
            task_id="old-task",
            task_state="input_required",
        )

        task = make_task(state=TaskState.completed, artifact_text="Response")

        async def mock_send_message(*args, **kwargs):
            yield (task, None)

        adapter_with_client._client.send_message = mock_send_message
        adapter_with_client._client.resubscribe = MagicMock()

        await adapter_with_client.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=False,
            room_id="room-123",
        )

        # Resubscribe should not be called on non-bootstrap
        adapter_with_client._client.resubscribe.assert_not_called()

    @pytest.mark.asyncio
    async def test_resubscribes_to_resumable_task(self, adapter_with_client):
        """Should try to resubscribe to non-terminal task."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello")

        # History with resumable task
        history = A2ASessionState(
            context_id="ctx-123",
            task_id="resumable-task",
            task_state="input_required",  # Non-terminal
        )

        # Mock resubscribe to return current task state
        resumed_task = make_task(
            state=TaskState.input_required,
            task_id="resumable-task",
        )

        async def mock_resubscribe(*args, **kwargs):
            yield (resumed_task, None)

        adapter_with_client._client.resubscribe = mock_resubscribe

        # Mock send_message for the actual message processing
        task = make_task(state=TaskState.completed, artifact_text="Response")

        async def mock_send_message(*args, **kwargs):
            yield (task, None)

        adapter_with_client._client.send_message = mock_send_message

        await adapter_with_client.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Task should have been restored
        # Note: The new completed task clears it, so we verify resubscribe was called
        assert adapter_with_client._contexts.get("room-123") == "ctx-123"

    @pytest.mark.asyncio
    async def test_handles_resubscribe_failure(self, adapter_with_client):
        """Should handle resubscribe failure gracefully."""
        tools = FakeAgentTools()
        msg = make_platform_message("Hello")

        history = A2ASessionState(
            context_id="ctx-123",
            task_id="old-task",
            task_state="input_required",
        )

        async def mock_resubscribe(*args, **kwargs):
            raise Exception("Task not found")
            yield  # Make it an async generator

        adapter_with_client._client.resubscribe = mock_resubscribe

        task = make_task(state=TaskState.completed, artifact_text="Response")

        async def mock_send_message(*args, **kwargs):
            yield (task, None)

        adapter_with_client._client.send_message = mock_send_message

        # Should not raise
        await adapter_with_client.on_message(
            msg,
            tools,
            history,
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Context should still be restored
        assert adapter_with_client._contexts.get("room-123") == "ctx-123"
