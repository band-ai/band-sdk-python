"""Tests for Agent lifecycle wiring to the adapter.

Room-scoped ``on_cleanup`` is exercised by the runtime tests; these cover
the adapter-wide ``cleanup_all`` hook that Agent.stop() invokes so owned
resources (e.g. a CLI runtime subprocess) don't outlive the agent.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.agent import Agent
from band.core.run.cancellation import ExecutionCancellation
from band.testing import FakeAgentTools
from tests.core.contractsupport import agent_input


def make_agent(adapter: object) -> Agent:
    runtime = AsyncMock()
    runtime.stop.return_value = True
    runtime.claim_single_instance = MagicMock()
    runtime.release_single_instance = MagicMock()
    agent = Agent(runtime=runtime, adapter=adapter)  # type: ignore[arg-type]
    agent._started = True
    return agent


class TestStartFailureCleansUpAdapter:
    @pytest.mark.asyncio
    async def test_runtime_start_failure_rolls_back_adapter(self):
        """on_started may spawn resources; a failed runtime.start must free them."""
        adapter = AsyncMock()
        runtime = AsyncMock()
        runtime.agent_name = ""
        runtime.agent_description = ""
        runtime.start.side_effect = RuntimeError("websocket refused")
        runtime.claim_single_instance = MagicMock()
        runtime.release_single_instance = MagicMock()
        agent = Agent(runtime=runtime, adapter=adapter)  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="websocket refused"):
            await agent.start()

        adapter.cleanup_all.assert_awaited_once()
        runtime.claim_single_instance.assert_called_once()
        runtime.release_single_instance.assert_called_once()


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
            async def handle_turn(self, inp: object) -> None: ...
            async def on_cleanup(self, room_id: str) -> None: ...
            async def on_started(self, name: str, description: str) -> None: ...

        agent = make_agent(MinimalAdapter())

        assert await agent.stop() is True


@pytest.mark.asyncio
async def test_agent_execution_passes_the_runtime_cancellation_token() -> None:
    """Agent wires the runtime interrupt into the turn's tools proxy."""
    from band.core.backends.observing import turn_context

    captured: dict[str, object] = {}

    async def handle_turn(inp: object) -> None:
        captured["cancellation"] = turn_context(inp.tools).cancellation  # type: ignore[attr-defined]

    adapter = AsyncMock()
    adapter.handle_turn = handle_turn
    agent = make_agent(adapter)
    input_for_turn = agent_input(FakeAgentTools(room_id="room-execution"))

    class Preprocessor:
        async def process(self, **_kwargs):
            return input_for_turn

    agent._preprocessor = Preprocessor()
    context = SimpleNamespace(_interrupt_kind=None, _pending_interrupt=None)

    await agent._on_execute(context, object())

    assert isinstance(captured["cancellation"], ExecutionCancellation)
    assert captured["cancellation"]._ctx is context
