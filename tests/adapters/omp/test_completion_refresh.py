"""Prove post-prompt refresh failure retires its runtime before recovery."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.integrations.omprpc import OmpCompletionTimeout, OmpProcessError
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.omp.kit import RefreshClients, run_turn
from tests.baseline.tools import BaselineTools
from tests.integrations.omppeer import COMPLETE, REFRESH_LOSS_SCENARIO
from tests.integrations.omprpc.kit import (
    COMPLETION_BUDGET_S,
    OMP_PEER_SCRIPT,
    peer_process,
    process_is_alive,
    state_requests_observed,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock

TURN_DEADLINE_S = 30.0


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestPostPromptRefresh:
    async def test_lost_refresh_response_retires_before_a_recovered_turn(
        self, tmp_path: Path, refresh_clients: RefreshClients
    ) -> None:
        async with controlled_clock() as clock:
            adapter = OmpAdapter(
                OmpAdapterConfig(
                    cwd=str(tmp_path),
                    command=(
                        sys.executable,
                        "-u",
                        OMP_PEER_SCRIPT,
                        "--scenario",
                        REFRESH_LOSS_SCENARIO,
                    ),
                    turn_timeout_s=TURN_DEADLINE_S,
                ),
                emit=(),
            )
            tools = FakeAgentTools()
            try:
                async with clock.background(run_turn(adapter, tools)) as running:
                    runtime = await refresh_clients.created.get()
                    await clock.run_io(runtime.prompt_completed.wait())
                    await state_requests_observed(
                        runtime, runtime.state_requests_after_prompt + 1
                    )
                    failed_peer = await peer_process(runtime)
                    assert not running.done()

                    await clock.elapse(COMPLETION_BUDGET_S)

                    with pytest.raises(OmpCompletionTimeout):
                        await clock.run_io(running)

                assert [
                    failure["provider"] for failure in reported_failures(tools)
                ] == ["omp"]
                with pytest.raises(OmpProcessError):
                    await runtime.request("get_state")
                assert not await asyncio.to_thread(process_is_alive, failed_peer)

                recovered_tools = BaselineTools()
                await clock.run_io(run_turn(adapter, recovered_tools, bootstrap=False))

                assert recovered_tools.chat == [COMPLETE]
                assert recovered_tools.turn.replied
                assert recovered_tools.turn.complete
                assert not reported_failures(recovered_tools)
                assert len(refresh_clients.clients) == 2
                replacement = refresh_clients.clients[-1]
                assert replacement is not runtime
                recovered_peer = await peer_process(replacement)
                assert recovered_peer != failed_peer
                assert await asyncio.to_thread(process_is_alive, recovered_peer)
            finally:
                await clock.run_io(adapter.cleanup_all())
