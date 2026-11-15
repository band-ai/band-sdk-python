"""Post-prompt bookkeeping timeout does not fail an already-replied turn."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.omp.failures import (
    BoomGetStateAfterReplyRuntime,
    HangAfterPromptRuntime,
    LeftoverHostToolRuntime,
)
from tests.adapters.omp.runtime import AdapterFactory
from tests.adapters.omp.turns import run_turn
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock

TURN_TIMEOUT_S = 1.0


class TestTimeoutAfterReply:
    @pytest.mark.usefixtures("native_startup_ready")
    @pytest.mark.asyncio
    @pytest.mark.timeout(15)
    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_post_prompt_timeout_does_not_fail_a_replied_turn(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = HangAfterPromptRuntime()
        monkeypatch.setattr(
            "band.adapters.omp.lifecycle.OmpRpcClient", lambda *a, **kw: runtime
        )
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), turn_timeout_s=TURN_TIMEOUT_S), emit=()
        )
        tools = FakeAgentTools()
        async with (
            controlled_clock() as clock,
            clock.background(run_turn(adapter, tools)) as turn,
        ):
            await runtime.hung.wait()
            assert tools.turn.replied
            await clock.elapse(TURN_TIMEOUT_S)
            with pytest.raises(TimeoutError):
                await turn
        assert tools.turn.replied
        assert not any(
            failure.get("code") == "timeout" for failure in reported_failures(tools)
        )

    @pytest.mark.asyncio
    async def test_post_prompt_get_state_error_does_not_fail_a_replied_turn(
        self, tmp_path: Path, adapter_for: AdapterFactory
    ):
        runtime = BoomGetStateAfterReplyRuntime()
        adapter = adapter_for(
            runtime, config=OmpAdapterConfig(cwd=str(tmp_path)), emit=()
        )
        tools = FakeAgentTools()
        with pytest.raises(RuntimeError, match="state failed"):
            await run_turn(adapter, tools)
        assert tools.turn.replied
        assert reported_failures(tools) == []

    @pytest.mark.asyncio
    async def test_leftover_host_tool_cannot_reply_after_turn_closes(
        self, tmp_path: Path, adapter_for: AdapterFactory
    ):
        runtime = LeftoverHostToolRuntime()
        adapter = adapter_for(
            runtime, config=OmpAdapterConfig(cwd=str(tmp_path)), emit=()
        )
        tools = FakeAgentTools()
        with pytest.raises(RuntimeError, match="state failed"):
            await run_turn(adapter, tools)
        assert runtime.leftover is not None
        await runtime.started.wait()
        runtime.release.set()
        await runtime.leftover
        assert tools.turn.replied
        assert reported_failures(tools) == []
        assert [call["arguments"]["content"] for call in tools.tool_calls] == [
            "Already replied"
        ]
