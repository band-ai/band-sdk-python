"""Tests for Agent lifecycle wiring to the adapter.

Room-scoped ``on_cleanup`` is exercised by the runtime tests; these cover
the adapter-wide ``cleanup_all`` hook that Agent.stop() invokes so owned
resources (e.g. a CLI runtime subprocess) don't outlive the agent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from band.agent import Agent
from band.core.model_catalog import ModelSelection, ModelSelectionError
from tests.catalogs import CatalogAdapter


def make_agent(adapter: object, *, started: bool = True) -> Agent:
    runtime = AsyncMock()
    runtime.stop.return_value = True
    runtime.feature_flags = None
    runtime.claim_single_instance = MagicMock()
    runtime.release_single_instance = MagicMock()
    agent = Agent(runtime=runtime, adapter=adapter)  # type: ignore[arg-type]
    agent._started = started
    return agent


class TestStartFailureCleansUpAdapter:
    @pytest.mark.asyncio
    async def test_runtime_start_failure_rolls_back_adapter(self):
        """on_started may spawn resources; a failed runtime.start must free them."""
        adapter = AsyncMock()
        runtime = AsyncMock()
        runtime.start.side_effect = RuntimeError("websocket refused")
        agent = Agent(runtime=runtime, adapter=adapter)  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="websocket refused"):
            await agent.start()

        adapter.cleanup_all.assert_awaited_once()


class TestStartValidatesModelSelection:
    @pytest.mark.asyncio
    async def test_an_advertised_selection_starts_after_on_started_lists_it(
        self,
    ) -> None:
        agent = make_agent(
            CatalogAdapter(ModelSelection(model="sonnet", reasoning_effort="high")),
            started=False,
        )

        await agent.start()

        agent._runtime.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_rejected_selection_fails_start_before_connecting(self) -> None:
        adapter = CatalogAdapter(ModelSelection(model="gpt-9"))
        agent = make_agent(adapter, started=False)

        with pytest.raises(ModelSelectionError, match='model "gpt-9"'):
            await agent.start()

        agent._runtime.start.assert_not_awaited()
        assert adapter.cleaned_up

    @pytest.mark.asyncio
    async def test_a_failing_release_does_not_mask_the_rejection(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        adapter = CatalogAdapter(ModelSelection(model="gpt-9"), cleanup_fails=True)

        with pytest.raises(ModelSelectionError, match='model "gpt-9"'):
            await make_agent(adapter, started=False).start()

        assert "Adapter cleanup_all failed" in caplog.text


class TestStopCleansUpAdapter:
    @pytest.mark.asyncio
    async def test_stop_calls_adapter_cleanup_all(self):
        adapter = AsyncMock()
        agent = make_agent(adapter)

        assert await agent.stop() is True

        adapter.cleanup_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_cleans_up_adapter_even_when_runtime_stop_raises(self):
        """A broken websocket close must not leak adapter-owned resources."""
        adapter = AsyncMock()
        agent = make_agent(adapter)
        agent._runtime.stop.side_effect = RuntimeError("close failed")

        with pytest.raises(RuntimeError, match="close failed"):
            await agent.stop()

        adapter.cleanup_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_survives_cleanup_all_failure(self):
        """A failing adapter cleanup must not break shutdown."""
        adapter = AsyncMock()
        adapter.cleanup_all.side_effect = RuntimeError("runtime already gone")
        agent = make_agent(adapter)

        assert await agent.stop() is True

    @pytest.mark.asyncio
    async def test_stop_tolerates_adapter_without_cleanup_all(self):
        """Bare FrameworkAdapter implementations without the hook still stop."""

        class MinimalAdapter:
            async def on_event(self, inp: object) -> None: ...
            async def on_cleanup(self, room_id: str) -> None: ...
            async def on_started(self, name: str, description: str) -> None: ...

        agent = make_agent(MinimalAdapter())

        assert await agent.stop() is True
