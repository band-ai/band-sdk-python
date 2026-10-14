"""TestStopCleansUpAdapter"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.core.agentlifecycle.kit import make_agent


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
