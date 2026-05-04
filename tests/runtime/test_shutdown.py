"""Tests for graceful shutdown utilities."""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from thenvoi.runtime.shutdown import GracefulShutdown, run_with_graceful_shutdown

# Scope the warning filter to this module only — _handle_signal creates a
# task for _shutdown that may not be awaited during synchronous test teardown.
pytestmark = pytest.mark.filterwarnings(
    "ignore:coroutine 'GracefulShutdown._shutdown' was never awaited:RuntimeWarning"
)


class FakeAgent:
    """Minimal agent stand-in (plain object to avoid unawaited coroutine warnings)."""

    def __init__(self):
        self.stop = AsyncMock(return_value=True)
        self.run = AsyncMock(
            return_value=None
        )  # explicit return to avoid MagicMock propagation
        self.is_running = True


@pytest.fixture
def mock_agent():
    """Create a FakeAgent for testing shutdown handlers."""
    return FakeAgent()


class TestGracefulShutdownInit:
    """Test GracefulShutdown initialization."""

    def test_init_stores_agent(self, mock_agent):
        """Should store the agent reference."""
        shutdown = GracefulShutdown(mock_agent)
        assert shutdown.agent is mock_agent

    def test_init_default_timeout(self, mock_agent):
        """Should have default timeout of 30 seconds."""
        shutdown = GracefulShutdown(mock_agent)
        assert shutdown.timeout == 30.0

    def test_init_custom_timeout(self, mock_agent):
        """Should accept custom timeout."""
        shutdown = GracefulShutdown(mock_agent, timeout=60.0)
        assert shutdown.timeout == 60.0

    def test_init_on_signal_callback(self, mock_agent):
        """Should accept on_signal callback."""
        callback = MagicMock()
        shutdown = GracefulShutdown(mock_agent, on_signal=callback)
        assert shutdown.on_signal is callback


class TestGracefulShutdownSignalRegistration:
    """Test signal handler registration."""

    async def test_register_signals_sets_registered_flag(self, mock_agent):
        """register_signals() should set _registered flag."""
        shutdown = GracefulShutdown(mock_agent)

        with patch.object(
            asyncio.get_running_loop(), "add_signal_handler", MagicMock()
        ):
            shutdown.register_signals()

        assert shutdown._registered is True
        shutdown.unregister_signals()

    async def test_register_signals_idempotent(self, mock_agent):
        """register_signals() should be idempotent."""
        shutdown = GracefulShutdown(mock_agent)
        loop = asyncio.get_running_loop()

        with patch.object(loop, "add_signal_handler", MagicMock()) as mock_add:
            shutdown.register_signals()
            shutdown.register_signals()  # Second call

            # Should only register once
            # SIGINT + SIGTERM + SIGHUP (on Unix)
            expected_calls = 2  # SIGINT and SIGTERM minimum
            if hasattr(signal, "SIGHUP"):
                expected_calls = 3
            assert mock_add.call_count == expected_calls

        shutdown.unregister_signals()

    async def test_unregister_signals_clears_flag(self, mock_agent):
        """unregister_signals() should clear _registered flag."""
        shutdown = GracefulShutdown(mock_agent)
        loop = asyncio.get_running_loop()

        with patch.object(loop, "add_signal_handler", MagicMock()):
            with patch.object(loop, "remove_signal_handler", MagicMock()):
                shutdown.register_signals()
                shutdown.unregister_signals()

        assert shutdown._registered is False

    async def test_unregister_signals_when_not_registered_is_safe(self, mock_agent):
        """unregister_signals() when not registered should be safe."""
        shutdown = GracefulShutdown(mock_agent)
        shutdown.unregister_signals()  # Should not raise
        assert shutdown._registered is False


class TestGracefulShutdownHandler:
    """Test signal handling behavior."""

    async def test_handle_signal_calls_on_signal_callback(self, mock_agent):
        """_handle_signal should invoke on_signal callback."""
        callback = MagicMock()
        shutdown = GracefulShutdown(mock_agent, on_signal=callback)
        shutdown._shutdown_event = asyncio.Event()

        # Mock asyncio.create_task to prevent actual shutdown
        with patch("asyncio.create_task"):
            shutdown._handle_signal(signal.SIGINT)

        callback.assert_called_once_with(signal.SIGINT)

    async def test_handle_signal_sets_shutdown_event(self, mock_agent):
        """_handle_signal should set shutdown event."""
        shutdown = GracefulShutdown(mock_agent)
        shutdown._shutdown_event = asyncio.Event()

        with patch("asyncio.create_task"):
            shutdown._handle_signal(signal.SIGTERM)

        assert shutdown._shutdown_event.is_set()

    async def test_shutdown_calls_agent_stop_with_timeout(self, mock_agent):
        """_shutdown should call agent.stop with timeout."""
        shutdown = GracefulShutdown(mock_agent, timeout=15.0)

        await shutdown._shutdown()

        mock_agent.stop.assert_called_once_with(timeout=15.0)

    async def test_shutdown_handles_exception(self, mock_agent):
        """_shutdown should handle exceptions gracefully."""
        mock_agent.stop = AsyncMock(side_effect=Exception("Stop failed"))
        shutdown = GracefulShutdown(mock_agent)

        # Should not raise
        await shutdown._shutdown()

    async def test_handle_signal_guards_before_callback(self, mock_agent):
        """Second signal during shutdown must not re-invoke on_signal or re-set event."""
        callback = MagicMock()
        shutdown = GracefulShutdown(mock_agent, on_signal=callback)
        shutdown._shutdown_event = asyncio.Event()

        with patch("asyncio.create_task") as mock_create_task:
            # Simulate create_task returning a sentinel so _shutdown_task is set.
            sentinel_task = MagicMock()
            mock_create_task.return_value = sentinel_task

            # First signal: callback fires, event is set, task is scheduled.
            shutdown._handle_signal(signal.SIGINT)
            assert callback.call_count == 1
            assert shutdown._shutdown_event.is_set()
            assert mock_create_task.call_count == 1

            # Reset event so we can detect if the guard lets the second signal
            # re-set it (it must not).
            shutdown._shutdown_event.clear()

            # Second signal during shutdown: everything must be skipped.
            shutdown._handle_signal(signal.SIGTERM)
            assert callback.call_count == 1, (
                "on_signal should not fire for duplicate signals"
            )
            assert not shutdown._shutdown_event.is_set(), (
                "shutdown event must not be re-set for duplicate signals"
            )
            assert mock_create_task.call_count == 1, (
                "no additional shutdown task should be created"
            )

    async def test_shutdown_shields_agent_stop_from_cancellation(self, mock_agent):
        """_shutdown should let agent.stop finish even when the task is cancelled."""
        stop_started = asyncio.Event()
        stop_finished = asyncio.Event()

        async def slow_stop(*, timeout):  # noqa: ARG001
            stop_started.set()
            # Multiple small sleeps give the cancellation a chance to arrive
            # while agent.stop is mid-flight, which is the scenario under test.
            for _ in range(5):
                await asyncio.sleep(0.01)
            stop_finished.set()
            return True

        mock_agent.stop = AsyncMock(side_effect=slow_stop)
        shutdown = GracefulShutdown(mock_agent, timeout=1.0)

        task = asyncio.create_task(shutdown._shutdown())
        await stop_started.wait()

        # Cancel the outer shutdown task mid-stop. Shielded agent.stop should
        # still run to completion.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Shielded stop should complete shortly after the outer task was cancelled.
        try:
            await asyncio.wait_for(stop_finished.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pytest.fail(
                "agent.stop was cancelled mid-flight; asyncio.shield is missing"
            )

    async def test_shutdown_reraises_cancelled_error(self, mock_agent):
        """_shutdown should log and re-raise CancelledError without a second stop."""
        stop_calls: list[float] = []

        async def tracking_stop(*, timeout):
            stop_calls.append(timeout)
            raise asyncio.CancelledError()

        mock_agent.stop = AsyncMock(side_effect=tracking_stop)
        shutdown = GracefulShutdown(mock_agent, timeout=5.0)

        with pytest.raises(asyncio.CancelledError):
            await shutdown._shutdown()

        # Only one call — the except CancelledError branch must not kick off a
        # second concurrent agent.stop. (Here agent.stop raises directly, so
        # no background task exists; the shield's keep-running-in-background
        # behavior is covered by test_shutdown_shields_agent_stop_from_cancellation.)
        assert stop_calls == [5.0]


class TestGracefulShutdownWaitForShutdown:
    """Test wait_for_shutdown behavior."""

    async def test_wait_for_shutdown_blocks_until_event_set(self, mock_agent):
        """wait_for_shutdown should block until shutdown event is set."""
        shutdown = GracefulShutdown(mock_agent)
        shutdown._shutdown_event = asyncio.Event()

        # Set event after a short delay
        async def set_event_later():
            await asyncio.sleep(0.05)
            shutdown._shutdown_event.set()

        asyncio.create_task(set_event_later())

        start = asyncio.get_running_loop().time()
        await shutdown.wait_for_shutdown()
        elapsed = asyncio.get_running_loop().time() - start

        assert elapsed >= 0.04  # Should have waited

    async def test_wait_for_shutdown_creates_event_if_needed(self, mock_agent):
        """wait_for_shutdown should create event if not exists."""
        shutdown = GracefulShutdown(mock_agent)
        assert shutdown._shutdown_event is None

        # Set immediately to avoid blocking
        async def set_immediately():
            # Wait for wait_for_shutdown to create the event
            for _ in range(10):
                await asyncio.sleep(0.01)
                if shutdown._shutdown_event:
                    shutdown._shutdown_event.set()
                    return

        task = asyncio.create_task(set_immediately())

        # This should create the event and wait for it
        await asyncio.wait_for(shutdown.wait_for_shutdown(), timeout=1.0)

        # Event should have been created
        assert shutdown._shutdown_event is not None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestGracefulShutdownContextManager:
    """Test async context manager protocol."""

    async def test_aenter_registers_signals(self, mock_agent):
        """__aenter__ should register signals."""
        shutdown = GracefulShutdown(mock_agent)
        loop = asyncio.get_running_loop()

        with patch.object(loop, "add_signal_handler", MagicMock()):
            async with shutdown:
                assert shutdown._registered is True

    async def test_aexit_unregisters_signals(self, mock_agent):
        """__aexit__ should unregister signals."""
        shutdown = GracefulShutdown(mock_agent)
        loop = asyncio.get_running_loop()

        with patch.object(loop, "add_signal_handler", MagicMock()):
            with patch.object(loop, "remove_signal_handler", MagicMock()):
                async with shutdown:
                    pass
                assert shutdown._registered is False


class TestRunWithGracefulShutdown:
    """Test the convenience function."""

    async def test_run_with_graceful_shutdown_registers_signals(self, mock_agent):
        """run_with_graceful_shutdown should register signal handlers."""
        loop = asyncio.get_running_loop()

        with patch.object(loop, "add_signal_handler", MagicMock()) as mock_add:
            with patch.object(loop, "remove_signal_handler", MagicMock()):
                await run_with_graceful_shutdown(mock_agent, timeout=10.0)

                # Should have registered signals
                assert mock_add.called

    async def test_run_with_graceful_shutdown_runs_agent(self, mock_agent):
        """run_with_graceful_shutdown should run the agent."""
        loop = asyncio.get_running_loop()

        with patch.object(loop, "add_signal_handler", MagicMock()):
            with patch.object(loop, "remove_signal_handler", MagicMock()):
                await run_with_graceful_shutdown(mock_agent)

                mock_agent.run.assert_called_once_with(shutdown_timeout=30.0)

    async def test_run_with_graceful_shutdown_uses_custom_timeout(self, mock_agent):
        """run_with_graceful_shutdown should use custom timeout."""
        loop = asyncio.get_running_loop()

        with patch.object(loop, "add_signal_handler", MagicMock()):
            with patch.object(loop, "remove_signal_handler", MagicMock()):
                await run_with_graceful_shutdown(mock_agent, timeout=60.0)

                mock_agent.run.assert_called_once_with(shutdown_timeout=60.0)

    async def test_run_with_graceful_shutdown_handles_cancelled_error(self, mock_agent):
        """run_with_graceful_shutdown should handle CancelledError."""
        mock_agent.run = AsyncMock(side_effect=asyncio.CancelledError())
        loop = asyncio.get_running_loop()

        with patch.object(loop, "add_signal_handler", MagicMock()):
            with patch.object(loop, "remove_signal_handler", MagicMock()):
                # Should not raise
                await run_with_graceful_shutdown(mock_agent)
