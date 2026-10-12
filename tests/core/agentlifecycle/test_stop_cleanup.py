"""Tests for Agent lifecycle wiring to the adapter.

Room-scoped ``on_cleanup`` is exercised by the runtime tests; these cover
the adapter-wide ``cleanup_all`` hook that Agent.stop() invokes so owned
resources (e.g. a CLI runtime subprocess) don't outlive the agent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.agent import Agent


def make_runtime() -> AsyncMock:
    runtime = AsyncMock()
    runtime.stop.return_value = True
    runtime.cleanup_pending = False
    runtime.claim_single_instance = MagicMock()
    runtime.release_single_instance = MagicMock()
    return runtime


async def make_agent(adapter: object) -> Agent:
    agent = Agent(runtime=make_runtime(), adapter=adapter)  # type: ignore[arg-type]
    await agent.start()
    return agent


class TestStartFailureCleansUpAdapter:
    @pytest.mark.asyncio
    async def test_runtime_start_failure_rolls_back_adapter(self):
        """on_started may spawn resources; a failed runtime.start must free them."""
        adapter = AsyncMock()
        runtime = make_runtime()
        runtime.start.side_effect = RuntimeError("websocket refused")
        agent = Agent(runtime=runtime, adapter=adapter)  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="websocket refused"):
            await agent.start()

        adapter.cleanup_all.assert_awaited_once()


class TestStopCleansUpAdapter:
    @pytest.mark.asyncio
    async def test_stop_calls_adapter_cleanup_all(self):
        adapter = AsyncMock()
        agent = await make_agent(adapter)

        assert await agent.stop() is True

        adapter.cleanup_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_cleans_up_adapter_even_when_runtime_stop_raises(self):
        """A broken websocket close must not leak adapter-owned resources."""
        adapter = AsyncMock()
        agent = await make_agent(adapter)
        agent._runtime.stop.side_effect = RuntimeError("close failed")

        with pytest.raises(ExceptionGroup) as failures:
            await agent.stop()
        assert failures.group_contains(RuntimeError, match="close failed")

        adapter.cleanup_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_reports_cleanup_all_failure(self):
        """A failing adapter cleanup is surfaced, never reported as clean."""
        adapter = AsyncMock()
        adapter.cleanup_all.side_effect = RuntimeError("runtime already gone")
        agent = await make_agent(adapter)

        with pytest.raises(ExceptionGroup) as failures:
            await agent.stop()
        assert failures.group_contains(RuntimeError, match="runtime already gone")

    @pytest.mark.asyncio
    async def test_stop_tolerates_adapter_without_cleanup_all(self):
        """Bare FrameworkAdapter implementations without the hook still stop."""

        class MinimalAdapter:
            async def on_event(self, inp: object) -> None: ...
            async def on_cleanup(self, room_id: str) -> None: ...
            async def on_started(self, name: str, description: str) -> None: ...

        agent = await make_agent(MinimalAdapter())

        assert await agent.stop() is True
