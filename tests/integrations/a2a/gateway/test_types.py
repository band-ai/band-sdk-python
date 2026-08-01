"""Tests for A2A Gateway types."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from a2a.types import Task, TaskState, TaskStatus, TaskStatusUpdateEvent

from band.integrations.a2a.gateway.types import GatewaySessionState, PendingA2ATask


class TestGatewaySessionState:
    """Tests for GatewaySessionState dataclass."""

    def test_init_default_empty_dicts(self) -> None:
        """Empty state should have empty dicts."""
        state = GatewaySessionState()
        assert state.context_to_room == {}
        assert state.room_participants == {}

    def test_init_with_context_mapping(self) -> None:
        """Can initialize with context_to_room mapping."""
        state = GatewaySessionState(
            context_to_room={"ctx-1": "room-1", "ctx-2": "room-2"}
        )
        assert state.context_to_room["ctx-1"] == "room-1"
        assert state.context_to_room["ctx-2"] == "room-2"
        assert state.room_participants == {}

    def test_init_with_participants(self) -> None:
        """Can initialize with room_participants mapping."""
        state = GatewaySessionState(
            room_participants={"room-1": {"peer-1", "peer-2"}, "room-2": {"peer-3"}}
        )
        assert state.context_to_room == {}
        assert state.room_participants["room-1"] == {"peer-1", "peer-2"}
        assert state.room_participants["room-2"] == {"peer-3"}

    def test_init_with_both(self) -> None:
        """Can initialize with both context_to_room and room_participants."""
        state = GatewaySessionState(
            context_to_room={"ctx-1": "room-1"},
            room_participants={"room-1": {"peer-1"}},
        )
        assert state.context_to_room == {"ctx-1": "room-1"}
        assert state.room_participants == {"room-1": {"peer-1"}}

    def test_context_to_room_dict_behavior(self) -> None:
        """context_to_room behaves like a normal dict."""
        state = GatewaySessionState()
        state.context_to_room["new-ctx"] = "new-room"
        assert state.context_to_room["new-ctx"] == "new-room"

    def test_room_participants_set_behavior(self) -> None:
        """room_participants values behave like sets."""
        state = GatewaySessionState(room_participants={"room-1": {"peer-1"}})
        state.room_participants["room-1"].add("peer-2")
        assert state.room_participants["room-1"] == {"peer-1", "peer-2"}


class TestPendingA2ATask:
    """Tests for PendingA2ATask dataclass."""

    @pytest.fixture
    def sample_task(self) -> Task:
        """Create a sample A2A Task for testing."""
        return Task(
            id=str(uuid4()),
            context_id=str(uuid4()),
            status=TaskStatus(state=TaskState.working),
        )

    @pytest.fixture
    def sample_queue(self) -> asyncio.Queue[TaskStatusUpdateEvent]:
        """Create a sample asyncio Queue for testing."""
        return asyncio.Queue()

    def test_init_with_task_and_queue(
        self, sample_task: Task, sample_queue: asyncio.Queue[TaskStatusUpdateEvent]
    ) -> None:
        """Can initialize with task and queue."""
        pending = PendingA2ATask(
            task=sample_task,
            sse_queue=sample_queue,
            peer_id="weather",
        )
        assert pending.task == sample_task
        assert pending.sse_queue == sample_queue

    def test_init_stores_peer_id(
        self, sample_task: Task, sample_queue: asyncio.Queue[TaskStatusUpdateEvent]
    ) -> None:
        """Stores peer_id for correlation."""
        pending = PendingA2ATask(
            task=sample_task,
            sse_queue=sample_queue,
            peer_id="servicenow",
        )
        assert pending.peer_id == "servicenow"

    def test_task_property_access(
        self, sample_task: Task, sample_queue: asyncio.Queue[TaskStatusUpdateEvent]
    ) -> None:
        """Can access task properties."""
        pending = PendingA2ATask(
            task=sample_task,
            sse_queue=sample_queue,
            peer_id="weather",
        )
        assert pending.task.id == sample_task.id
        assert pending.task.context_id == sample_task.context_id
        assert pending.task.status.state == TaskState.working

    def test_sse_queue_is_async_queue(
        self, sample_task: Task, sample_queue: asyncio.Queue[TaskStatusUpdateEvent]
    ) -> None:
        """Queue instance is asyncio.Queue."""
        pending = PendingA2ATask(
            task=sample_task,
            sse_queue=sample_queue,
            peer_id="weather",
        )
        assert isinstance(pending.sse_queue, asyncio.Queue)
