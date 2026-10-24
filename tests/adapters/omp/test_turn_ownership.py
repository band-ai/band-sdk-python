"""Native OMP reply ownership across background turns."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.testing import MISSING_REPLY_FAILURE, FakeAgentTools, failure_reports
from tests.adapters.omp.kit import PendingRuntime, run_turn


class TestTurnOwnership:
    @pytest.mark.asyncio
    async def test_background_reply_keeps_original_turn_owned(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = PendingRuntime()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        tools = FakeAgentTools()
        task = asyncio.create_task(run_turn(adapter, tools))
        await asyncio.wait_for(runtime.yielded.wait(), 1)
        assert not task.done()
        assert not failure_reports(tools)
        assert not tools.turn.replied
        runtime.finish.set()
        await task
        assert tools.turn.replied
        assert not failure_reports(tools)
        await adapter.cleanup_all()
        assert runtime.closed

    @pytest.mark.asyncio
    async def test_settled_without_terminal_tool_still_reports_failure(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = PendingRuntime()
        runtime.reply = False
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        tools = FakeAgentTools()
        with pytest.raises(TurnResultAlreadyReported):
            await run_turn(adapter, tools)
        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
        assert not tools.turn.replied
        await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_cancelled_background_turn_cannot_own_next_reply(
        self, monkeypatch, tmp_path: Path
    ):
        runtimes: asyncio.Queue[PendingRuntime] = asyncio.Queue()

        def spawn(*args, **kwargs):
            runtime = PendingRuntime()
            runtimes.put_nowait(runtime)
            return runtime

        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", spawn)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        old_tools = FakeAgentTools()
        pending = asyncio.create_task(run_turn(adapter, old_tools))
        old_runtime = await asyncio.wait_for(runtimes.get(), 1)
        await asyncio.wait_for(old_runtime.yielded.wait(), 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert old_runtime.closed
        new_tools = FakeAgentTools()
        new_tools.fetch_room_context = AsyncMock(return_value={"data": []})
        next_turn = asyncio.create_task(run_turn(adapter, new_tools, bootstrap=False))
        new_runtime = await asyncio.wait_for(runtimes.get(), 1)
        await asyncio.wait_for(new_runtime.yielded.wait(), 1)
        new_runtime.finish.set()
        await next_turn
        assert new_tools.turn.replied
        assert not old_tools.turn.replied
        assert not failure_reports(old_tools)
        await adapter.cleanup_all()
