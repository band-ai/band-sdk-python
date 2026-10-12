"""Tests for AgentRuntime."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.runtime.execution import ExecutionContext
from band.runtime.runtime import AgentRuntime

# Import test helpers from conftest
from tests.conftest import (
    make_message_event,
    make_participant_added_event,
    make_room_added_event,
    make_room_removed_event,
)
from tests.runtime.conftest import wait_for_condition
from tests.signalcases import INSTALL_SIGNAL_HANDLER_CASES, INSTALL_SIGNAL_HANDLER_IDS


@pytest.fixture
def mock_link():
    """Mock BandLink for testing AgentRuntime."""
    link = MagicMock()
    link.agent_id = "agent-123"
    link.is_connected = False

    # Async methods
    link.connect = AsyncMock()
    link.disconnect = AsyncMock()
    link.run_forever = AsyncMock()
    link.subscribe_agent_rooms = AsyncMock()
    link.subscribe_room = AsyncMock()
    link.unsubscribe_room = AsyncMock()

    # REST client mock
    link.rest = MagicMock()
    link.rest.agent_api_chats = MagicMock()
    link.rest.agent_api_chats.list_agent_chats = AsyncMock(
        return_value=MagicMock(data=[])
    )

    # Message lifecycle methods
    link.get_next_message = AsyncMock(return_value=None)
    link.get_stale_processing_messages = AsyncMock(return_value=[])
    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()

    # Make link iterable for async for
    async def empty_aiter():
        return
        yield

    link.__aiter__ = lambda self: empty_aiter()
    link.rest.agent_api_participants = MagicMock()
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_context = MagicMock()
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(data=[])
    )

    return link


@pytest.fixture
def mock_handler():
    """Mock execution handler."""
    return AsyncMock()


class TestAgentRuntimeConstruction:
    """Test AgentRuntime initialization."""

    def test_init_stores_link(self, mock_link, mock_handler):
        """Should store link and agent_id."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        assert runtime.link is mock_link
        assert runtime.agent_id == "agent-123"

    def test_init_creates_presence(self, mock_link, mock_handler):
        """Should create RoomPresence."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        assert runtime.presence is not None
        assert runtime.presence.link is mock_link

    def test_init_empty_executions(self, mock_link, mock_handler):
        """Should start with no executions."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        assert runtime.executions == {}
        assert runtime.active_sessions == {}

    def test_init_with_room_filter(self, mock_link, mock_handler):
        """Should accept room filter."""

        def my_filter(room):
            return room.get("type") == "task"

        runtime = AgentRuntime(
            mock_link, "agent-123", mock_handler, room_filter=my_filter
        )

        assert runtime.presence.room_filter is my_filter


class TestAgentRuntimeLifecycle:
    """Test AgentRuntime start/stop lifecycle."""

    async def test_start_starts_presence(self, mock_link, mock_handler):
        """start() should start RoomPresence."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Mock presence.start to track it was called
        runtime.presence.start = AsyncMock()

        await runtime.start()

        runtime.presence.start.assert_called_once()

    async def test_stop_stops_all_executions(self, mock_link, mock_handler):
        """stop() should stop all execution contexts."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Create some executions
        await runtime._create_execution("room-1")
        await runtime._create_execution("room-2")

        assert len(runtime.executions) == 2

        await runtime.stop()

        assert len(runtime.executions) == 0

    async def test_stop_calls_cleanup_callback(self, mock_link, mock_handler):
        """stop() should call on_session_cleanup for each room."""
        cleanup_rooms = []

        async def on_cleanup(room_id):
            cleanup_rooms.append(room_id)

        runtime = AgentRuntime(
            mock_link, "agent-123", mock_handler, on_session_cleanup=on_cleanup
        )

        await runtime._create_execution("room-1")
        await runtime._create_execution("room-2")

        await runtime.stop()

        assert set(cleanup_rooms) == {"room-1", "room-2"}


class TestAgentRuntimeExecutionManagement:
    """Test execution context management."""

    async def test_shutdown_rejects_new_joins_until_restart(
        self, mock_link, mock_handler
    ):
        cleanup_started = asyncio.Event()
        finish_cleanup = asyncio.Event()
        cleaned_rooms: list[str] = []

        async def cleanup(room_id: str) -> None:
            cleaned_rooms.append(room_id)
            cleanup_started.set()
            await finish_cleanup.wait()

        runtime = AgentRuntime(
            mock_link, "agent-123", mock_handler, on_session_cleanup=cleanup
        )
        await runtime.presence.on_room_joined("room-1", {})
        stopping = asyncio.create_task(runtime.stop())
        try:
            await cleanup_started.wait()
            await runtime.presence.on_room_joined("room-1", {})
            await runtime.presence.on_room_joined("room-2", {})
            assert runtime.active_sessions == {}
        finally:
            finish_cleanup.set()
            await stopping
        assert cleaned_rooms == ["room-1"]
        await runtime.presence.on_room_joined("room-2", {})
        assert runtime.active_sessions == {}
        await runtime.start()
        try:
            await runtime.presence.on_room_joined("room-2", {})
            await _settled(runtime, "room-2")
            assert set(runtime.active_sessions) == {"room-2"}
        finally:
            await runtime.stop()

    async def test_creates_execution_on_room_joined(self, mock_link, mock_handler):
        """Room joined should create execution context."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        await _joined(runtime, "room-123")

        assert "room-123" in runtime.executions
        assert isinstance(runtime.executions["room-123"], ExecutionContext)

        # Cleanup
        await runtime.stop()

    async def test_destroys_execution_on_room_left(self, mock_link, mock_handler):
        """Room left should destroy execution context."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        await runtime._create_execution("room-123")
        await runtime._on_room_left("room-123")

        assert "room-123" not in runtime.executions

    async def test_execution_idempotent(self, mock_link, mock_handler):
        """Creating execution twice should return same instance."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        exec1 = await runtime._create_execution("room-123")
        exec2 = await runtime._create_execution("room-123")

        assert exec1 is exec2

        await runtime.stop()

    async def test_all_contexts_share_one_claim_registry(self, mock_link, mock_handler):
        """Message ownership must be shared across every context the runtime
        creates — including a context recreated after room removal — while
        retaining room-local ownership state."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        room_a = await runtime._create_execution("room-a")
        room_b = await runtime._create_execution("room-b")
        await _left(runtime, "room-a")
        room_a_again = await runtime._create_execution("room-a")

        assert room_a.claims is room_b.claims
        assert room_a_again.claims is room_a.claims

        await runtime.stop()

    async def test_pending_ack_is_retried_only_by_its_room(
        self, mock_link, mock_handler
    ):
        """A shared registry must not acknowledge a different room's message."""
        mock_link.mark_processed = AsyncMock(return_value=True)
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)
        room_a = await runtime._create_execution("room-a")
        room_b = await runtime._create_execution("room-b")
        room_a.claims.remember_ack_pending("room-a", "msg-1")

        assert await room_b._retry_pending_processed_acks()
        mock_link.mark_processed.assert_not_awaited()

        assert await room_a._retry_pending_processed_acks()
        mock_link.mark_processed.assert_awaited_once_with("room-a", "msg-1")

        await runtime.stop()

    async def test_room_recreation_reclaims_completed_and_retries_pending_ack(
        self, mock_link, mock_handler
    ):
        """Room teardown may drop durable cache, but not unacknowledged success."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)
        original = await runtime._create_execution("room-a")
        original.claims.remember_completed("room-a", "msg-completed")
        original.claims.remember_ack_pending("room-a", "msg-ack-pending")

        await _left(runtime, "room-a")
        recreated = await runtime._create_execution("room-a")

        assert not recreated.claims.is_completed("room-a", "msg-completed")

        mock_link.mark_processed = AsyncMock(return_value=True)
        assert await recreated._retry_pending_processed_acks()
        mock_link.mark_processed.assert_awaited_once_with("room-a", "msg-ack-pending")

        await runtime.stop()

    async def test_active_sessions_returns_copy(self, mock_link, mock_handler):
        """active_sessions should return a copy."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        await runtime._create_execution("room-123")
        sessions = runtime.active_sessions

        # Modifying returned dict shouldn't affect internal state
        sessions["room-999"] = MagicMock()

        assert "room-999" not in runtime.executions

        await runtime.stop()


class TestAgentRuntimeEventRouting:
    """Test event routing to executions."""

    async def test_routes_events_to_execution(self, mock_link, mock_handler):
        """Room events should be routed to execution context."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        await runtime._create_execution("room-123")

        event = make_message_event(
            room_id="room-123",
            msg_id="msg-1",
            content="Hello",
        )
        await runtime._on_room_event("room-123", event)

        # Event should be in execution's queue
        assert runtime.executions["room-123"].queue.qsize() == 1

        await runtime.stop()

    async def test_ignores_events_for_unknown_room(self, mock_link, mock_handler):
        """Events for unknown rooms should be ignored."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        event = make_message_event(
            room_id="unknown-room",
            msg_id="msg-1",
        )

        # Should not raise
        await runtime._on_room_event("unknown-room", event)

    async def test_routes_participant_callbacks_to_execution(
        self, mock_link, mock_handler
    ):
        """Participant callbacks should fire through the execution context."""
        on_participant_added = AsyncMock()
        runtime = AgentRuntime(
            mock_link,
            "agent-123",
            mock_handler,
            on_participant_added=on_participant_added,
        )

        await runtime._create_execution("room-123")

        event = make_participant_added_event(
            room_id="room-123",
            participant_id="user-2",
            name="User Two",
        )
        await runtime._on_room_event("room-123", event)
        await asyncio.sleep(0.1)

        on_participant_added.assert_awaited_once()

        await runtime.stop()


class TestAgentRuntimeCustomFactory:
    """Test custom execution factory."""

    async def test_uses_custom_factory(self, mock_link, mock_handler):
        """Should use custom execution factory when provided."""
        custom_execution = MagicMock()
        custom_execution.start = AsyncMock()
        custom_execution.stop = AsyncMock()

        def my_factory(room_id, link):
            custom_execution.room_id = room_id
            return custom_execution

        runtime = AgentRuntime(
            mock_link, "agent-123", mock_handler, execution_factory=my_factory
        )

        await runtime._create_execution("room-123")

        assert runtime.executions["room-123"] is custom_execution
        assert custom_execution.room_id == "room-123"
        custom_execution.start.assert_called_once()

        await runtime.stop()


class TestAgentRuntimePresenceIntegration:
    """Test integration between AgentRuntime and RoomPresence."""

    async def test_presence_callbacks_wired(self, mock_link, mock_handler):
        """RoomPresence callbacks should be wired to runtime methods."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Verify callbacks are set (not None)
        assert runtime.presence.on_room_joined is not None
        assert runtime.presence.on_room_left is not None
        assert runtime.presence.on_room_event is not None

        # Verify they work correctly by calling them
        await runtime.presence.on_room_joined("room-test", {"id": "room-test"})
        await _settled(runtime, "room-test")
        assert "room-test" in runtime.executions

        await runtime.presence.on_room_left("room-test")
        assert "room-test" not in runtime.executions


class TestAgentRuntimeRun:
    """Test AgentRuntime.run() method."""

    @pytest.mark.parametrize(
        ("kwargs", "installs"),
        INSTALL_SIGNAL_HANDLER_CASES,
        ids=INSTALL_SIGNAL_HANDLER_IDS,
    )
    async def test_run_starts_and_runs_forever(
        self, mock_link, mock_handler, kwargs, installs
    ):
        """run() hands the host's signal choice to link.run_forever()."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        # Mock start to track it was called
        runtime.start = AsyncMock()
        runtime.stop = AsyncMock()

        await runtime.run(**kwargs)

        runtime.start.assert_called_once()
        mock_link.run_forever.assert_awaited_once_with(install_signal_handlers=installs)
        runtime.stop.assert_called_once()

    async def test_run_stops_on_error(self, mock_link, mock_handler):
        """run() should stop even if run_forever raises."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)

        mock_link.run_forever.side_effect = Exception("Connection lost")
        runtime.start = AsyncMock()
        runtime.stop = AsyncMock()

        with pytest.raises(Exception, match="Connection lost"):
            await runtime.run()

        runtime.stop.assert_called_once()

    async def test_stop_wakes_run_when_host_owns_signals(self, mock_link, mock_handler):
        """stop() disconnects so run(install_signal_handlers=False) can return."""
        runtime = AgentRuntime(mock_link, "agent-123", mock_handler)
        runtime.presence.start = AsyncMock()
        runtime.presence.stop = AsyncMock()
        blocked = asyncio.Event()

        async def block_until_disconnected(**kwargs: object) -> None:
            await blocked.wait()

        async def disconnect_and_unblock() -> None:
            blocked.set()

        mock_link.run_forever.side_effect = block_until_disconnected
        mock_link.disconnect.side_effect = disconnect_and_unblock

        running = asyncio.create_task(runtime.run(install_signal_handlers=False))
        await asyncio.sleep(0)
        await runtime.stop()
        await asyncio.wait_for(running, timeout=1.0)

        mock_link.disconnect.assert_awaited()
        # Host stop() plus run()'s finally both tear down.
        assert runtime.presence.stop.await_count >= 1


class RoomCleanup:
    """The adapter's room cleanup, held or failed on demand."""

    def __init__(self) -> None:
        self.rooms: list[str] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.failing = False
        self.failing_rooms: set[str] = set()

    def hold(self) -> None:
        self.entered.clear()
        self.release.clear()

    async def __call__(self, room_id: str) -> None:
        self.entered.set()
        await self.release.wait()
        if self.failing or room_id in self.failing_rooms:
            raise RuntimeError("room cleanup failed")
        self.rooms.append(room_id)


@pytest.fixture
def room_cleanup() -> RoomCleanup:
    return RoomCleanup()


@pytest.fixture
async def runtime(mock_link, mock_handler, room_cleanup):
    runtime = AgentRuntime(
        mock_link, "agent-123", mock_handler, on_session_cleanup=room_cleanup
    )
    yield runtime
    room_cleanup.failing = False
    room_cleanup.release.set()
    await runtime.stop()


class TestAgentRuntimeRoomCleanupOwnership:
    """A retiring execution stays owned and fenced until its cleanup succeeds."""

    async def test_held_cleanup_fences_rejoin_until_it_succeeds(
        self, runtime: AgentRuntime, room_cleanup: RoomCleanup, mock_handler
    ):
        await _joined(runtime, "room-1")
        old = runtime.executions["room-1"]
        room_cleanup.hold()
        leaving = asyncio.create_task(runtime._on_room_left("room-1"))
        await room_cleanup.entered.wait()

        assert await runtime._create_execution("room-1") is None
        await runtime._on_room_event("room-1", make_message_event(room_id="room-1"))
        assert runtime.active_sessions == {}
        assert runtime.cleanup_pending

        room_cleanup.release.set()
        await leaving
        await asyncio.wait_for(_admitted(runtime, "room-1"), timeout=1)
        assert runtime.executions["room-1"] is not old
        assert not old.is_running
        assert not runtime.cleanup_pending
        mock_handler.assert_not_awaited()

    @pytest.mark.parametrize("retry", ["reconnect", "event", "rejoin"])
    async def test_failed_cleanup_is_retained_and_retried_by_admission(
        self, runtime: AgentRuntime, room_cleanup: RoomCleanup, retry: str
    ):
        await _joined(runtime, "room-1")
        room_cleanup.failing = True
        await _left(runtime, "room-1")
        assert runtime.cleanup_pending

        await runtime._on_room_joined("room-1", {})
        await _settled(runtime, "room-1")
        assert "room-1" not in runtime.executions

        room_cleanup.failing = False
        match retry:
            case "reconnect":
                await runtime._on_reconnected()
            case "event":
                await runtime._on_room_event(
                    "room-1", make_message_event(room_id="room-1")
                )
            case "rejoin":
                await runtime._on_room_joined("room-1", {})
        await asyncio.wait_for(_admitted(runtime, "room-1"), timeout=1)
        assert room_cleanup.rooms == ["room-1"]
        assert not runtime.cleanup_pending

    async def test_newer_leave_invalidates_pending_admission(
        self, runtime: AgentRuntime, room_cleanup: RoomCleanup
    ):
        await _joined(runtime, "room-1")
        room_cleanup.hold()
        leaving = asyncio.create_task(runtime._on_room_left("room-1"))
        await room_cleanup.entered.wait()
        await runtime._on_room_joined("room-1", {})
        left_again = asyncio.create_task(runtime._on_room_left("room-1"))

        room_cleanup.release.set()
        await asyncio.gather(leaving, left_again)
        await _settled(runtime, "room-1")
        assert runtime.active_sessions == {}
        assert room_cleanup.rooms == ["room-1"]

    async def test_stop_attempts_every_room_and_retries_failed_cleanup(
        self, runtime: AgentRuntime, room_cleanup: RoomCleanup, mock_link
    ):
        await _joined(runtime, "room-1")
        await _joined(runtime, "room-2")
        room_cleanup.failing = True
        with pytest.raises(ExceptionGroup) as failures:
            await runtime.stop()
        assert len(failures.value.exceptions) == 2
        mock_link.disconnect.assert_awaited()
        assert runtime.cleanup_pending

        room_cleanup.failing = False
        assert await runtime.stop()
        assert sorted(room_cleanup.rooms) == ["room-1", "room-2"]
        assert not runtime.cleanup_pending

    async def test_cancellation_resistant_execution_keeps_room_fenced(
        self, mock_link, room_cleanup: RoomCleanup, monkeypatch
    ):
        monkeypatch.setattr("band.runtime.execution.CYCLE_CANCEL_GRACE_SECONDS", 0.01)
        started, finish = asyncio.Event(), asyncio.Event()

        async def stubborn(ctx: ExecutionContext, event) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await finish.wait()
                raise

        runtime = AgentRuntime(
            mock_link, "agent-123", stubborn, on_session_cleanup=room_cleanup
        )
        await _joined(runtime, "room-1")
        await runtime._on_room_event("room-1", make_message_event(room_id="room-1"))
        await started.wait()
        await _left(runtime, "room-1")
        assert runtime.cleanup_pending
        assert room_cleanup.rooms == []

        await runtime._on_room_joined("room-1", {})
        await _settled(runtime, "room-1")
        assert "room-1" not in runtime.executions

        finish.set()
        await runtime._on_reconnected()
        await asyncio.wait_for(_admitted(runtime, "room-1"), timeout=1)
        assert room_cleanup.rooms == ["room-1"]
        await runtime.stop()


async def _settled(runtime: AgentRuntime, room_id: str) -> None:
    """Let the room's owned lifecycle task finish, whatever its outcome."""
    record = runtime._rooms.get(room_id)
    if record is not None and record.task is not None:
        await asyncio.wait({record.task})


async def _joined(runtime: AgentRuntime, room_id: str) -> None:
    """Join as presence does, then let the requested admission settle."""
    await runtime._on_room_joined(room_id, {})
    await _settled(runtime, room_id)


async def _left(runtime: AgentRuntime, room_id: str) -> None:
    """Leave as presence does, then let the requested cleanup settle."""
    await runtime._on_room_left(room_id)
    await _settled(runtime, room_id)


async def _admitted(runtime: AgentRuntime, room_id: str) -> None:
    while room_id not in runtime.executions:
        await _settled(runtime, room_id)
        await asyncio.sleep(0)


class HeldExecution:
    """A custom execution whose start can be held or failed."""

    def __init__(self, room_id: str, gate: StartGate) -> None:
        self.room_id = room_id
        self.gate = gate
        self.started = False
        self.stopped = False
        self.events: list[object] = []

    async def start(self) -> None:
        self.gate.entered.set()
        await self.gate.release.wait()
        if self.gate.failures:
            self.gate.failures -= 1
            raise RuntimeError("execution start failed")
        self.started = True

    async def stop(self, timeout: float | None = None) -> bool:
        self.stopped = True
        return self.gate.graceful

    async def on_event(self, event: object) -> None:
        self.events.append(event)


class StartGate:
    """Builds ``HeldExecution``s; each start waits for ``release``."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.failures = 0
        self.graceful = True
        self.built: list[HeldExecution] = []

    def hold(self) -> None:
        self.entered.clear()
        self.release.clear()

    def __call__(self, room_id: str, link, *, hub_room_id=None) -> HeldExecution:
        execution = HeldExecution(room_id, self)
        self.built.append(execution)
        return execution


@pytest.fixture
def start_gate() -> StartGate:
    return StartGate()


@pytest.fixture
async def held_runtime(mock_link, mock_handler, room_cleanup, start_gate):
    runtime = AgentRuntime(
        mock_link,
        "agent-123",
        mock_handler,
        execution_factory=start_gate,
        on_session_cleanup=room_cleanup,
    )
    yield runtime
    start_gate.release.set()
    room_cleanup.failing = False
    room_cleanup.release.set()
    await runtime.stop()


class TestAgentRuntimeStartupOwnership:
    """Execution startup is owned with retirement and routed only when ready."""

    @pytest.mark.parametrize("withdrawal", ["leave", "stop"])
    async def test_held_start_never_routes_after_withdrawal(
        self,
        held_runtime: AgentRuntime,
        start_gate: StartGate,
        room_cleanup: RoomCleanup,
        withdrawal: str,
    ):
        start_gate.hold()
        joining = asyncio.create_task(held_runtime._on_room_joined("room-1", {}))
        await start_gate.entered.wait()
        assert held_runtime.active_sessions == {}

        if withdrawal == "leave":
            await held_runtime._on_room_left("room-1")
            withdrawn = _settled(held_runtime, "room-1")
        else:
            withdrawn = held_runtime.stop()
        start_gate.release.set()
        await asyncio.gather(joining, withdrawn)

        (execution,) = start_gate.built
        assert execution.started and execution.stopped
        assert held_runtime.active_sessions == {}
        assert room_cleanup.rooms == ["room-1"]

    async def test_failed_start_is_not_routed_and_is_retried(
        self, held_runtime: AgentRuntime, start_gate: StartGate
    ):
        start_gate.failures = 1
        await _joined(held_runtime, "room-1")
        assert held_runtime.active_sessions == {}
        assert held_runtime.cleanup_pending

        await held_runtime._on_room_event(
            "room-1", make_message_event(room_id="room-1")
        )
        await asyncio.wait_for(_admitted(held_runtime, "room-1"), timeout=1)
        failed, admitted = start_gate.built
        assert failed.stopped and not failed.started
        assert held_runtime.executions["room-1"] is admitted
        assert not held_runtime.cleanup_pending

    async def test_rejoin_during_held_start_admits_a_fresh_execution(
        self, held_runtime: AgentRuntime, start_gate: StartGate
    ):
        start_gate.hold()
        joining = asyncio.create_task(held_runtime._on_room_joined("room-1", {}))
        await start_gate.entered.wait()
        await held_runtime._on_room_left("room-1")
        assert await held_runtime._create_execution("room-1") is None

        start_gate.release.set()
        await joining
        await asyncio.wait_for(_admitted(held_runtime, "room-1"), timeout=1)
        withdrawn, admitted = start_gate.built
        assert withdrawn.stopped
        assert held_runtime.executions["room-1"] is admitted


async def test_stop_retry_keeps_an_earlier_cancelled_room_reported(
    held_runtime: AgentRuntime, start_gate: StartGate, room_cleanup: RoomCleanup
):
    await _joined(held_runtime, "room-1")
    await _joined(held_runtime, "room-2")
    start_gate.graceful = False
    room_cleanup.failing_rooms = {"room-2"}
    with pytest.raises(ExceptionGroup):
        await held_runtime.stop()

    start_gate.graceful = True
    room_cleanup.failing_rooms = set()
    assert await held_runtime.stop() is False
    assert sorted(room_cleanup.rooms) == ["room-1", "room-2"]


class QueueLink:
    """Feeds platform events to the real presence consumer."""

    def __init__(self, link: MagicMock) -> None:
        self.events: asyncio.Queue[object] = asyncio.Queue()

        async def stream():
            while True:
                yield await self.events.get()

        link.__aiter__ = lambda _self: stream()


async def test_held_room_cleanup_does_not_block_other_rooms_events(
    mock_link, mock_handler, room_cleanup: RoomCleanup
):
    feed = QueueLink(mock_link)
    handled = asyncio.Event()
    mock_handler.side_effect = lambda ctx, event: handled.set()
    runtime = AgentRuntime(
        mock_link, "agent-123", mock_handler, on_session_cleanup=room_cleanup
    )
    await runtime.start()
    try:
        feed.events.put_nowait(make_room_added_event("room-1"))
        feed.events.put_nowait(make_room_added_event("room-2"))
        await wait_for_condition(lambda: "room-2" in runtime.executions)

        room_cleanup.hold()
        feed.events.put_nowait(make_room_removed_event("room-1"))
        await room_cleanup.entered.wait()
        feed.events.put_nowait(make_message_event(room_id="room-2", msg_id="m-2"))
        await asyncio.wait_for(handled.wait(), timeout=1)
        assert room_cleanup.rooms == []
        assert "room-1" not in runtime.executions
    finally:
        room_cleanup.release.set()
        await runtime.stop()


async def test_leave_behind_a_held_start_is_consumed_and_wins(
    mock_link, mock_handler, room_cleanup: RoomCleanup, start_gate: StartGate
):
    feed = QueueLink(mock_link)
    runtime = AgentRuntime(
        mock_link,
        "agent-123",
        mock_handler,
        execution_factory=start_gate,
        on_session_cleanup=room_cleanup,
    )
    await runtime.start()
    try:
        start_gate.hold()
        feed.events.put_nowait(make_room_added_event("room-1"))
        await start_gate.entered.wait()
        feed.events.put_nowait(make_room_removed_event("room-1"))
        await wait_for_condition(lambda: not runtime._rooms["room-1"].wanted)

        start_gate.release.set()
        await _settled(runtime, "room-1")
        (execution,) = start_gate.built
        assert execution.started and execution.stopped
        assert runtime.active_sessions == {}
        assert room_cleanup.rooms == ["room-1"]
    finally:
        start_gate.release.set()
        await runtime.stop()
