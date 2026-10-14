"""Agent compositor: Stop."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from band.agent import Agent, running_agents
from band.core.exceptions import CleanupIncompleteError


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

    @pytest.mark.asyncio
    async def test_concurrent_stops_tear_down_once(self, mock_runtime, mock_adapter):
        """A host handler's stop() racing __aexit__'s stop() tears down once."""

        async def stop_that_yields(timeout: float | None = None) -> bool:
            await asyncio.sleep(0)
            return True

        mock_runtime.stop.side_effect = stop_that_yields
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()

        await asyncio.gather(agent.stop(), agent.stop())

        mock_runtime.stop.assert_awaited_once()
        mock_adapter.cleanup_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_during_start_is_honored_after_start(
        self, mock_runtime, mock_adapter
    ):
        """A host stop() mid-start tears down once start finishes."""
        start_entered = asyncio.Event()
        release_start = asyncio.Event()

        async def blocked_start(**kwargs: object) -> None:
            start_entered.set()
            await release_start.wait()

        mock_runtime.start.side_effect = blocked_start
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        starting = asyncio.create_task(agent.start())
        await start_entered.wait()

        # Returns immediately; start() applies the request when it finishes.
        assert await agent.stop(timeout=12.0) is True
        mock_runtime.stop.assert_not_awaited()

        release_start.set()
        await starting

        mock_runtime.stop.assert_awaited_once_with(timeout=12.0)
        assert not agent.is_running

    @pytest.mark.asyncio
    async def test_idle_stop_does_not_poison_later_start(
        self, mock_runtime, mock_adapter
    ):
        """stop() when idle stays a no-op and does not latch a deferred stop."""
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        assert await agent.stop() is True
        await agent.start()

        mock_runtime.stop.assert_not_awaited()
        assert agent.is_running

    @pytest.mark.asyncio
    async def test_failed_start_clears_pending_stop(self, mock_runtime, mock_adapter):
        """A mid-start stop must not tear down a later successful start."""
        start_entered = asyncio.Event()
        release_start = asyncio.Event()
        attempts = 0

        async def start_then_fail_once(**kwargs: object) -> None:
            nonlocal attempts
            attempts += 1
            start_entered.set()
            await release_start.wait()
            if attempts == 1:
                raise RuntimeError("boot failed")

        mock_runtime.start.side_effect = start_then_fail_once
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        starting = asyncio.create_task(agent.start())
        await start_entered.wait()
        assert await agent.stop(timeout=7.0) is True
        release_start.set()
        with pytest.raises(RuntimeError, match="boot failed"):
            await starting

        start_entered.clear()
        release_start.clear()
        starting_again = asyncio.create_task(agent.start())
        await start_entered.wait()
        release_start.set()
        await starting_again

        mock_runtime.stop.assert_not_awaited()
        assert agent.is_running

    @pytest.mark.asyncio
    async def test_stop_during_failed_start_unwind_does_not_leak(
        self, mock_runtime, mock_adapter
    ):
        """A stop requested while a failed start is still unwinding must not
        tear down the next successful start."""
        unwinding, release = asyncio.Event(), asyncio.Event()

        async def held_cleanup() -> None:
            unwinding.set()
            await release.wait()

        mock_runtime.start.side_effect = [RuntimeError("boot failed"), None]
        mock_adapter.cleanup_all = AsyncMock(side_effect=held_cleanup)
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        starting = asyncio.create_task(agent.start())
        await unwinding.wait()
        assert await agent.stop(timeout=7.0) is True
        release.set()
        with pytest.raises(RuntimeError, match="boot failed"):
            await starting

        mock_adapter.cleanup_all.side_effect = None
        await agent.start()
        assert agent.is_running
        mock_runtime.stop.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_returns_cleanly_after_stop_during_start(
        self, mock_runtime, mock_adapter
    ):
        """Mid-start stop leaves run() finished without awaiting run_forever."""
        start_entered = asyncio.Event()
        release_start = asyncio.Event()

        async def blocked_start(**kwargs: object) -> None:
            start_entered.set()
            await release_start.wait()

        mock_runtime.start.side_effect = blocked_start
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        async def stop_once_start_blocks() -> None:
            await start_entered.wait()
            await agent.stop(timeout=5.0)
            release_start.set()

        stopper = asyncio.create_task(stop_once_start_blocks())
        await agent.run(install_signal_handlers=False)
        await stopper

        mock_runtime.stop.assert_awaited_once_with(timeout=5.0)
        mock_runtime.run_forever.assert_not_awaited()
        assert not agent.is_running

    @pytest.mark.asyncio
    async def test_calls_adapter_cleanup_all(self, mock_runtime, mock_adapter):
        """Should give the adapter its shutdown teardown hook."""
        mock_runtime.stop.return_value = True
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        await agent.start()
        await agent.stop()

        mock_adapter.cleanup_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_adapter_cleanup_surfaces_and_stays_retryable(
        self, mock_runtime, mock_adapter
    ):
        """Explicit stop reports teardown failure and retries only that phase."""
        mock_runtime.stop.return_value = True
        mock_adapter.cleanup_all = AsyncMock(side_effect=Exception("teardown boom"))
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()

        with pytest.raises(ExceptionGroup, match="cleanup remains incomplete"):
            await agent.stop()
        mock_runtime.release_single_instance.assert_not_called()
        assert agent in running_agents()
        with pytest.raises(CleanupIncompleteError):
            await agent.start()

        mock_adapter.cleanup_all.side_effect = None
        assert await agent.stop() is True
        mock_runtime.stop.assert_awaited_once()
        assert mock_adapter.cleanup_all.await_count == 2
        mock_runtime.release_single_instance.assert_called_once()
        assert agent not in running_agents()

    @pytest.mark.asyncio
    async def test_retry_keeps_the_first_attempts_cancelled_turns_reported(
        self, mock_runtime, mock_adapter
    ):
        mock_runtime.stop.return_value = False
        mock_adapter.cleanup_all = AsyncMock(side_effect=[Exception("boom"), None])
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()

        with pytest.raises(ExceptionGroup):
            await agent.stop()
        assert await agent.stop() is False
        mock_runtime.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_runtime_cleanup_failure_still_disposes_the_adapter(
        self, mock_runtime, mock_adapter
    ):
        mock_runtime.stop.side_effect = [ExceptionGroup("rooms", [OSError()]), True]
        mock_adapter.cleanup_all = AsyncMock()
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()

        with pytest.raises(ExceptionGroup):
            await agent.stop()
        mock_adapter.cleanup_all.assert_awaited_once()
        assert await agent.stop() is True
        assert mock_runtime.stop.await_count == 2
        mock_adapter.cleanup_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancelled_cleanup_is_not_marked_clean(
        self, mock_runtime, mock_adapter
    ):
        entered, release = asyncio.Event(), asyncio.Event()

        async def held_cleanup() -> None:
            entered.set()
            await release.wait()

        mock_adapter.cleanup_all = AsyncMock(side_effect=held_cleanup)
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)
        await agent.start()
        stopping = asyncio.create_task(agent.stop())
        await entered.wait()
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        mock_runtime.release_single_instance.assert_not_called()

        release.set()
        await agent.stop()
        mock_runtime.release_single_instance.assert_called_once()

    @pytest.mark.asyncio
    async def test_body_error_survives_failed_cleanup(self, mock_runtime, mock_adapter):
        mock_adapter.cleanup_all = AsyncMock(side_effect=Exception("teardown boom"))
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        with pytest.raises(LookupError, match="body failed"):
            async with agent:
                raise LookupError("body failed")
        mock_runtime.release_single_instance.assert_not_called()
        mock_adapter.cleanup_all.side_effect = None
        await agent.stop()
        mock_runtime.release_single_instance.assert_called_once()

    @pytest.mark.asyncio
    async def test_startup_error_survives_failed_unwind_and_keeps_guard(
        self, mock_runtime, mock_adapter
    ):
        mock_runtime.start.side_effect = RuntimeError("boot failed")
        mock_adapter.cleanup_all = AsyncMock(side_effect=Exception("teardown boom"))
        agent = Agent(runtime=mock_runtime, adapter=mock_adapter)

        with pytest.raises(RuntimeError, match="boot failed"):
            await agent.start()
        mock_runtime.release_single_instance.assert_not_called()
        mock_adapter.cleanup_all.side_effect = None
        assert await agent.stop() is True
        mock_runtime.stop.assert_not_awaited()
        mock_runtime.release_single_instance.assert_called_once()
