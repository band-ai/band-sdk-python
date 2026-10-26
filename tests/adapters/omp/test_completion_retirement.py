"""Surviving callbacks retain room resources until they finish."""

from __future__ import annotations

import pytest

from band.integrations.omprpc import OmpCompletionTimeout, OmpProcessError
from band.testing import reported_failures
from tests.adapters.omp.completion import RetirementContext
from tests.adapters.omp.turns import run_turn
from tests.baseline.tools import BaselineTools
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omprpc.completion import COMPLETION_BUDGET_S
from tests.toolkit.clock import binary_clock_resolution

WORKSPACE_CONTENDER_ID = "workspace-contender"


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestCallbackRetirement:
    async def test_timeout_reports_one_failure_without_claiming_a_reply(
        self, retirement_turn: RetirementContext
    ) -> None:
        async with retirement_turn as turn:
            await turn.clock.elapse(COMPLETION_BUDGET_S)
            with pytest.raises(OmpCompletionTimeout):
                await turn.clock.run_io(turn.running)

            assert [
                failure["provider"] for failure in reported_failures(turn.tools)
            ] == ["omp"]
            assert not turn.tools.turn.replied

    async def test_same_room_recovers_only_after_callback_retirement(
        self, retirement_turn: RetirementContext
    ) -> None:
        async with retirement_turn as turn:
            await turn.clock.elapse(COMPLETION_BUDGET_S)
            with pytest.raises(OmpCompletionTimeout):
                await turn.clock.run_io(turn.running)

            with pytest.raises(OmpProcessError, match="retirement is unproven"):
                await turn.clock.run_io(
                    run_turn(turn.adapter, BaselineTools(), bootstrap=False)
                )
            assert turn.clients.clients == [turn.runtime]
            plugin = turn.clients.plugins[0]
            assert plugin.is_dir()

            await turn.hold.release()
            await turn.hold.retired.wait()
            recovered = BaselineTools()
            await turn.clock.run_io(run_turn(turn.adapter, recovered, bootstrap=False))

            assert recovered.chat == [COMPLETE]
            assert not plugin.exists()

    async def test_other_room_cannot_take_workspace_before_callback_retirement(
        self, retirement_turn: RetirementContext
    ) -> None:
        async with retirement_turn as turn:
            await turn.clock.elapse(COMPLETION_BUDGET_S)
            with pytest.raises(OmpCompletionTimeout):
                await turn.clock.run_io(turn.running)

            with pytest.raises(ValueError, match="assigned .* to both"):
                await run_turn(
                    turn.adapter, BaselineTools(), room_id=WORKSPACE_CONTENDER_ID
                )
            assert turn.clients.clients == [turn.runtime]

            await turn.hold.release()
            await turn.hold.retired.wait()
            await turn.clock.run_io(turn.adapter.on_cleanup("room-123"))
            contender = BaselineTools()
            await turn.clock.run_io(
                run_turn(turn.adapter, contender, room_id=WORKSPACE_CONTENDER_ID)
            )

            assert contender.chat == [COMPLETE]
