"""Tests for Agent compositor."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thenvoi.agent import Agent, DEFAULT_SHUTDOWN_TIMEOUT
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AgentInput
from thenvoi.runtime.types import AgentConfig, SessionConfig
from thenvoi.preprocessing.default import DefaultPreprocessor


@pytest.fixture
def mock_adapter():
    """Create mock adapter."""
    adapter = AsyncMock()
    adapter.on_started = AsyncMock()
    adapter.on_cleanup = AsyncMock()
    adapter.on_event = AsyncMock()
    return adapter


@pytest.fixture
def mock_runtime():
    """Create mock PlatformRuntime."""
    runtime = MagicMock()
    runtime.agent_name = "TestBot"
    runtime.agent_description = "A test bot"
    runtime.agent_id = "agent-123"
    runtime.initialize = AsyncMock()
    runtime.start = AsyncMock()
    runtime.stop = AsyncMock()
    runtime.run_forever = AsyncMock()
    return runtime


@pytest.fixture
def mock_preprocessor():
    """Create mock Preprocessor."""
    preprocessor = AsyncMock()
    preprocessor.process = AsyncMock(return_value=None)
    return preprocessor


class TestInitialization:
    """Tests for Agent initialization."""

    def test_full_composition(self, mock_runtime, mock_adapter, mock_preprocessor):
        """Should accept runtime, adapter, and preprocessor."""
        agent = Agent(
            runtime=mock_runtime,
            adapter=mock_adapter,
            preprocessor=mock_preprocessor,
        )

        assert agent._runtime is mock_runtime
        assert agent._adapter is mock_adapter
        assert agent._preprocessor is mock_preprocessor

    def test_default_preprocessor(self, mock_runtime, mock_adapter):
        """Should use DefaultPreprocessor if none provided."""
        agent = Agent(
            runtime=mock_runtime,
            adapter=mock_adapter,
        )

        assert isinstance(agent._preprocessor, DefaultPreprocessor)


class TestCreateFactory:
    """Tests for Agent.create() factory method."""

    def test_creates_with_default_urls(self, mock_adapter):
        """Should create agent with default URLs."""
        with patch("thenvoi.agent.PlatformRuntime") as mock_runtime_class:
            mock_runtime = MagicMock()
            mock_runtime_class.return_value = mock_runtime

            Agent.create(
                adapter=mock_adapter,
                agent_id="agent-123",
                api_key="test-key",
            )

            mock_runtime_class.assert_called_once_with(
                agent_id="agent-123",
                api_key="test-key",
                ws_url="wss://app.band.ai/api/v1/socket/websocket",
                rest_url="https://app.band.ai",
                config=None,
                session_config=None,
                contact_config=None,
                on_participant_added=None,
                on_participant_removed=None,
            )

    def test_creates_with_custom_urls(self, mock_adapter):
        """Should accept custom URLs."""
        with patch("thenvoi.agent.PlatformRuntime") as mock_runtime_class:
            mock_runtime = MagicMock()
            mock_runtime_class.return_value = mock_runtime

            Agent.create(
                adapter=mock_adapter,
                agent_id="agent-123",
                api_key="test-key",
                ws_url="wss://custom.example.com/ws",
                rest_url="https://custom.example.com",
            )

            call_kwargs = mock_runtime_class.call_args.kwargs
            assert call_kwargs["ws_url"] == "wss://custom.example.com/ws"
            assert call_kwargs["rest_url"] == "https://custom.example.com"

    def test_creates_with_configs(self, mock_adapter):
        """Should accept custom configs."""
        config = AgentConfig()
        session_config = SessionConfig()

        with patch("thenvoi.agent.PlatformRuntime") as mock_runtime_class:
            mock_runtime = MagicMock()
            mock_runtime_class.return_value = mock_runtime

            Agent.create(
                adapter=mock_adapter,
                agent_id="agent-123",
                api_key="test-key",
                config=config,
                session_config=session_config,
            )

            call_kwargs = mock_runtime_class.call_args.kwargs
            assert call_kwargs["config"] is config
            assert call_kwargs["session_config"] is session_config

    def test_creates_with_custom_preprocessor(self, mock_adapter, mock_preprocessor):
        """Should accept custom preprocessor."""
        with patch("thenvoi.agent.PlatformRuntime") as mock_runtime_class:
            mock_runtime = MagicMock()
            mock_runtime_class.return_value = mock_runtime

            agent = Agent.create(
                adapter=mock_adapter,
                agent_id="agent-123",
                api_key="test-key",
                preprocessor=mock_preprocessor,
            )

            assert agent._preprocessor is mock_preprocessor

    def test_creates_with_participant_callbacks(self, mock_adapter):
        """Should pass participant callbacks through to PlatformRuntime."""
        with patch("thenvoi.agent.PlatformRuntime") as mock_runtime_class:
            mock_runtime = MagicMock()
            mock_runtime_class.return_value = mock_runtime
            on_participant_added = AsyncMock()
            on_participant_removed = AsyncMock()

            Agent.create(
                adapter=mock_adapter,
                agent_id="agent-123",
                api_key="test-key",
                on_participant_added=on_participant_added,
                on_participant_removed=on_participant_removed,
            )

            call_kwargs = mock_runtime_class.call_args.kwargs
            assert call_kwargs["on_participant_added"] is on_participant_added
            assert call_kwargs["on_participant_removed"] is on_participant_removed


class TestProperties:
    """Tests for Agent properties."""

    def test_runtime_property(self, mock_runtime, mock_adapter):
        """Should expose runtime property."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        assert agent.runtime is mock_runtime

    def test_agent_name_property(self, mock_runtime, mock_adapter):
        """Should delegate agent_name to runtime."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        assert agent.agent_name == "TestBot"

    def test_agent_description_property(self, mock_runtime, mock_adapter):
        """Should delegate agent_description to runtime."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        assert agent.agent_description == "A test bot"


class TestStart:
    """Tests for Agent.start() method."""

    @pytest.mark.asyncio
    async def test_starts_runtime(self, mock_runtime, mock_adapter):
        """Should start the platform runtime."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.start()

        mock_runtime.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_calls_adapter_on_started(self, mock_runtime, mock_adapter):
        """Should call adapter.on_started with agent metadata."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.start()

        mock_adapter.on_started.assert_awaited_once_with("TestBot", "A test bot")

    @pytest.mark.asyncio
    async def test_passes_on_execute_to_runtime(self, mock_runtime, mock_adapter):
        """Should pass _on_execute to runtime.start()."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.start()

        call_kwargs = mock_runtime.start.call_args.kwargs
        assert call_kwargs["on_execute"] == agent._on_execute

    @pytest.mark.asyncio
    async def test_passes_on_cleanup_to_runtime(self, mock_runtime, mock_adapter):
        """Should pass adapter.on_cleanup to runtime.start()."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.start()

        call_kwargs = mock_runtime.start.call_args.kwargs
        assert call_kwargs["on_cleanup"] == mock_adapter.on_cleanup


class TestStop:
    """Tests for Agent.stop() method."""

    @pytest.mark.asyncio
    async def test_stops_runtime(self, mock_runtime, mock_adapter):
        """Should stop the platform runtime."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        # Must start first since stop() returns early if not started
        await agent.start()
        await agent.stop()

        mock_runtime.stop.assert_awaited_once()


class TestRun:
    """Tests for Agent.run() method."""

    @pytest.mark.asyncio
    async def test_starts_then_runs_forever(self, mock_runtime, mock_adapter):
        """Should start and run forever."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.run()

        mock_runtime.start.assert_awaited_once()
        mock_runtime.run_forever.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stops_on_completion(self, mock_runtime, mock_adapter):
        """Should stop runtime after run_forever completes."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.run()

        mock_runtime.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stops_on_exception(self, mock_runtime, mock_adapter):
        """Should stop runtime even if run_forever raises."""
        mock_runtime.run_forever = AsyncMock(side_effect=Exception("Connection lost"))
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        with pytest.raises(Exception, match="Connection lost"):
            await agent.run()

        # Should still have called stop
        mock_runtime.stop.assert_awaited_once()


class TestOnExecute:
    """Tests for Agent._on_execute() method."""

    @pytest.mark.asyncio
    async def test_processes_event_with_preprocessor(
        self, mock_runtime, mock_adapter, mock_preprocessor
    ):
        """Should pass event to preprocessor."""
        agent = Agent(
            runtime=mock_runtime,
            adapter=mock_adapter,
            preprocessor=mock_preprocessor,
        )

        mock_ctx = MagicMock()
        mock_event = MagicMock()

        await agent._on_execute(mock_ctx, mock_event)

        mock_preprocessor.process.assert_awaited_once_with(
            ctx=mock_ctx,
            event=mock_event,
            agent_id="agent-123",
        )

    @pytest.mark.asyncio
    async def test_skips_when_preprocessor_returns_none(
        self, mock_runtime, mock_adapter, mock_preprocessor
    ):
        """Should not call adapter when preprocessor returns None."""
        mock_preprocessor.process.return_value = None
        agent = Agent(
            runtime=mock_runtime,
            adapter=mock_adapter,
            preprocessor=mock_preprocessor,
        )

        mock_ctx = MagicMock()
        mock_event = MagicMock()

        await agent._on_execute(mock_ctx, mock_event)

        mock_adapter.on_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_calls_adapter_on_event(
        self, mock_runtime, mock_adapter, mock_preprocessor
    ):
        """Should call adapter.on_event with AgentInput."""
        mock_input = MagicMock(spec=AgentInput)
        mock_preprocessor.process.return_value = mock_input

        agent = Agent(
            runtime=mock_runtime,
            adapter=mock_adapter,
            preprocessor=mock_preprocessor,
        )

        mock_ctx = MagicMock()
        mock_event = MagicMock()

        await agent._on_execute(mock_ctx, mock_event)

        mock_adapter.on_event.assert_awaited_once_with(mock_input)


class TestSimpleAdapterIntegration:
    """Tests for SimpleAdapter integration."""

    @pytest.mark.asyncio
    async def test_works_with_simple_adapter(self, mock_runtime):
        """Should work with SimpleAdapter subclass."""
        # Create a mock SimpleAdapter
        adapter = MagicMock(spec=SimpleAdapter)
        adapter.on_started = AsyncMock()
        adapter.on_cleanup = AsyncMock()
        adapter.on_event = AsyncMock()

        agent = Agent(runtime=mock_runtime, adapter=adapter)

        await agent.start()

        adapter.on_started.assert_awaited_once()


class TestDefaultPreprocessorIntegration:
    """Tests for DefaultPreprocessor behavior."""

    @pytest.mark.asyncio
    async def test_default_preprocessor_filters_non_message_events(
        self, mock_runtime, mock_adapter
    ):
        """DefaultPreprocessor should filter non-message events."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        # Create a non-MessageEvent (e.g., RoomAddedEvent)
        from thenvoi.platform.event import RoomAddedEvent
        from thenvoi.client.streaming import RoomAddedPayload

        mock_ctx = MagicMock()
        mock_event = RoomAddedEvent(
            room_id="room-123",
            payload=RoomAddedPayload(
                id="room-123",
                title="Test Room",
                inserted_at="2024-01-01T00:00:00Z",
                updated_at="2024-01-01T00:00:00Z",
            ),
        )

        await agent._on_execute(mock_ctx, mock_event)

        # Should not call adapter (event was filtered)
        mock_adapter.on_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_default_preprocessor_filters_participant_events(
        self, mock_runtime, mock_adapter
    ):
        """Participant events should not become adapter execution turns."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        from thenvoi.client.streaming import ParticipantAddedPayload
        from thenvoi.platform.event import ParticipantAddedEvent

        mock_ctx = MagicMock()
        mock_event = ParticipantAddedEvent(
            room_id="room-123",
            payload=ParticipantAddedPayload(
                id="user-123",
                name="Test User",
                type="User",
            ),
        )

        await agent._on_execute(mock_ctx, mock_event)

        mock_adapter.on_event.assert_not_awaited()


class TestStartupRaceCondition:
    """Tests for startup sequence timing.

    Verifies that adapter.on_started() is called BEFORE any message processing.
    This prevents a race condition where messages arrive before the system prompt is set.
    """

    @pytest.mark.asyncio
    async def test_adapter_on_started_before_first_message(self):
        """System prompt must be set before any message processing."""
        from thenvoi.client.streaming import MessageCreatedPayload, MessageMetadata
        from thenvoi.platform.event import MessageEvent
        from thenvoi.runtime.types import ConversationContext
        from datetime import datetime, timezone

        # Track the order of calls
        call_order = []

        class TrackingAdapter(SimpleAdapter):
            def __init__(self):
                super().__init__(history_converter=MagicMock())
                self._system_prompt = ""

            async def on_started(self, agent_name: str, agent_description: str) -> None:
                call_order.append("on_started")
                self._system_prompt = f"You are {agent_name}"

            async def on_message(
                self,
                msg,
                tools,
                history,
                participants_msg,
                contacts_msg,
                *,
                is_session_bootstrap: bool,
                room_id: str,
            ) -> None:
                call_order.append("on_message")
                # This MUST NOT be empty if on_started was called first
                assert self._system_prompt != "", (
                    "System prompt was empty during message processing! "
                    "adapter.on_started() was not called before message arrived."
                )

            async def on_cleanup(self, room_id: str) -> None:
                pass

        adapter = TrackingAdapter()

        # Create a runtime that simulates immediate message delivery during start()
        class ImmediateMessageRuntime:
            agent_name = "TestBot"
            agent_description = "A test bot"
            agent_id = "agent-123"
            _on_execute = None

            async def initialize(self) -> None:
                """Initialize without starting message processing."""
                pass

            async def start(self, on_execute, on_cleanup=None):
                self._on_execute = on_execute

                # Create a proper MessageEvent that passes through preprocessor
                mock_ctx = MagicMock()
                mock_ctx.is_llm_initialized = False
                mock_ctx.mark_llm_initialized = MagicMock()
                mock_ctx.get_context = AsyncMock(
                    return_value=ConversationContext(
                        room_id="room-123",
                        messages=[],
                        participants=[],
                        hydrated_at=datetime.now(timezone.utc),
                    )
                )
                mock_ctx.participants = []
                mock_ctx.participants_changed = MagicMock(return_value=False)
                mock_ctx.room_id = "room-123"

                # Create a proper MessageEvent (not a MagicMock)
                message_event = MessageEvent(
                    room_id="room-123",
                    payload=MessageCreatedPayload(
                        id="msg-1",
                        content="Hello",
                        sender_id="user-1",
                        sender_type="User",
                        message_type="text",
                        metadata=MessageMetadata(mentions=[], status="sent"),
                        chat_room_id="room-123",
                        inserted_at=datetime.now(timezone.utc).isoformat(),
                        updated_at=datetime.now(timezone.utc).isoformat(),
                    ),
                )

                # This simulates what happens when a message arrives immediately
                await on_execute(mock_ctx, message_event)

            async def stop(self):
                pass

            async def run_forever(self):
                pass

        runtime = ImmediateMessageRuntime()
        agent = Agent(runtime=runtime, adapter=adapter)

        # This will fail if on_message is called before on_started
        await agent.start()

        # Verify on_started was called first
        assert len(call_order) >= 1, "No calls were made"
        assert call_order[0] == "on_started", (
            f"Expected on_started to be called first, but got: {call_order}"
        )


class TestIsRunningProperty:
    """Tests for Agent.is_running property."""

    def test_is_running_false_initially(self, mock_runtime, mock_adapter):
        """Should return False before start()."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        assert agent.is_running is False

    @pytest.mark.asyncio
    async def test_is_running_true_after_start(self, mock_runtime, mock_adapter):
        """Should return True after start()."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()
        assert agent.is_running is True

    @pytest.mark.asyncio
    async def test_is_running_false_after_stop(self, mock_runtime, mock_adapter):
        """Should return False after stop()."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()
        await agent.stop()
        assert agent.is_running is False


class TestGracefulStop:
    """Tests for Agent.stop() with timeout."""

    @pytest.mark.asyncio
    async def test_stop_without_timeout(self, mock_runtime, mock_adapter):
        """stop() without timeout should pass None to runtime."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()

        await agent.stop()

        mock_runtime.stop.assert_awaited_once_with(timeout=None)

    @pytest.mark.asyncio
    async def test_stop_with_timeout(self, mock_runtime, mock_adapter):
        """stop(timeout) should pass timeout to runtime."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()

        await agent.stop(timeout=30.0)

        mock_runtime.stop.assert_awaited_once_with(timeout=30.0)

    @pytest.mark.asyncio
    async def test_stop_returns_graceful_status(self, mock_runtime, mock_adapter):
        """stop() should return graceful status from runtime."""
        mock_runtime.stop.return_value = False  # Cancelled mid-processing
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()

        result = await agent.stop(timeout=1.0)

        assert result is False

    @pytest.mark.asyncio
    async def test_stop_returns_true_when_not_started(self, mock_runtime, mock_adapter):
        """stop() should return True when not started."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        result = await agent.stop(timeout=5.0)

        assert result is True
        mock_runtime.stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, mock_runtime, mock_adapter):
        """start() should be idempotent."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.start()
        await agent.start()  # Second call

        # Should only call runtime once
        assert mock_runtime.initialize.await_count == 1


class TestRunWithShutdownTimeout:
    """Tests for Agent.run() with shutdown_timeout."""

    @pytest.mark.asyncio
    async def test_run_uses_default_timeout(self, mock_runtime, mock_adapter):
        """run() should use default shutdown timeout."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.run()

        mock_runtime.stop.assert_awaited_once_with(timeout=DEFAULT_SHUTDOWN_TIMEOUT)

    @pytest.mark.asyncio
    async def test_run_uses_custom_timeout(self, mock_runtime, mock_adapter):
        """run() should use custom shutdown timeout."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.run(shutdown_timeout=60.0)

        mock_runtime.stop.assert_awaited_once_with(timeout=60.0)

    @pytest.mark.asyncio
    async def test_run_with_none_timeout(self, mock_runtime, mock_adapter):
        """run(shutdown_timeout=None) should stop immediately."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.run(shutdown_timeout=None)

        mock_runtime.stop.assert_awaited_once_with(timeout=None)


class TestAsyncContextManager:
    """Tests for Agent async context manager."""

    @pytest.mark.asyncio
    async def test_aenter_starts_agent(self, mock_runtime, mock_adapter):
        """__aenter__ should start the agent."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        async with agent:
            assert agent.is_running is True

    @pytest.mark.asyncio
    async def test_aenter_returns_agent(self, mock_runtime, mock_adapter):
        """__aenter__ should return the agent instance."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        async with agent as ctx:
            assert ctx is agent

    @pytest.mark.asyncio
    async def test_aexit_stops_agent(self, mock_runtime, mock_adapter):
        """__aexit__ should stop the agent."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        async with agent:
            pass

        assert agent.is_running is False
        mock_runtime.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aexit_uses_default_timeout(self, mock_runtime, mock_adapter):
        """__aexit__ should use default shutdown timeout."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        async with agent:
            pass

        mock_runtime.stop.assert_awaited_once_with(timeout=DEFAULT_SHUTDOWN_TIMEOUT)

    @pytest.mark.asyncio
    async def test_aexit_uses_run_timeout(self, mock_runtime, mock_adapter):
        """__aexit__ should use the timeout from run() if set."""
        mock_runtime.stop.return_value = True
        mock_runtime.run_forever = AsyncMock(side_effect=KeyboardInterrupt())
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        # Use try/except since run() will raise KeyboardInterrupt
        try:
            await agent.run(shutdown_timeout=60.0)
        except KeyboardInterrupt:
            pass

        # The finally block in run() should use the custom timeout
        mock_runtime.stop.assert_awaited_once_with(timeout=60.0)

    @pytest.mark.asyncio
    async def test_aexit_respects_none_timeout_from_run(
        self, mock_runtime, mock_adapter
    ):
        """__aexit__ should use None if run() was called with shutdown_timeout=None."""
        mock_runtime.stop.return_value = True
        mock_runtime.run_forever = AsyncMock(side_effect=KeyboardInterrupt())
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        # Use try/except since run() will raise KeyboardInterrupt
        try:
            await agent.run(shutdown_timeout=None)
        except KeyboardInterrupt:
            pass

        # The finally block in run() should use None (immediate cancellation)
        mock_runtime.stop.assert_awaited_once_with(timeout=None)

    @pytest.mark.asyncio
    async def test_aexit_stops_on_exception(self, mock_runtime, mock_adapter):
        """__aexit__ should stop agent even on exception."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        with pytest.raises(ValueError):
            async with agent:
                raise ValueError("Test error")

        mock_runtime.stop.assert_awaited_once()


class TestRunForever:
    """Tests for Agent.run_forever() method."""

    @pytest.mark.asyncio
    async def test_run_forever_delegates_to_runtime(self, mock_runtime, mock_adapter):
        """run_forever() should call runtime.run_forever()."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()

        await agent.run_forever()

        mock_runtime.run_forever.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_forever_works_with_context_manager(
        self, mock_runtime, mock_adapter
    ):
        """run_forever() should work inside async with block."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        async with agent:
            await agent.run_forever()

        mock_runtime.run_forever.assert_awaited_once()
