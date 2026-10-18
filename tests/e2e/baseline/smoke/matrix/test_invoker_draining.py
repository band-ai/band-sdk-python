"""OneShot shutdown drains accepted tools before releasing their resource."""

from __future__ import annotations

import pytest

from band.runtime.oneshot import OneShotInvoker
from tests.e2e.baseline.agents import per_adapter
from tests.e2e.baseline.smoke.matrix.cleanup import (
    ACTIVE_PROMPT,
    BUDGET,
    active_resource_turn,
    stopping_resource_host,
)
from tests.e2e.baseline.smoke.samples.sample_tools import EXECUTION_REPORTING
from tests.e2e.baseline.toolkit.capture import CaptureFactory
from tests.e2e.baseline.toolkit.provisioning import AdapterCell
from tests.e2e.baseline.toolkit.user_ops import UserOps


class TestInvokerDraining:
    @per_adapter(
        runs_tool_loop=True,
        prompt=ACTIVE_PROMPT,
        **EXECUTION_REPORTING,
    )
    @pytest.mark.timeout(extra=BUDGET.extra_s)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_shutdown_drains_tool_before_closing_its_resource(
        self,
        cell: AdapterCell,
        user_ops: UserOps,
        reply_capture: CaptureFactory,
    ) -> None:
        async with active_resource_turn(
            cell, user_ops, reply_capture, OneShotInvoker
        ) as turn:
            async with stopping_resource_host(turn) as stopping:
                assert not stopping.done()
                assert not turn.client.is_closed
            assert turn.work.settled
            assert not turn.work.resource_released_early
            assert turn.client.is_closed
