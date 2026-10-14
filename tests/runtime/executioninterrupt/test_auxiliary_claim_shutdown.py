"""Room shutdown while an auxiliary claim is in flight."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.runtime.execution import ExecutionContext
from tests.runtime.executioninterrupt.kit import (
    ClosingExecution,
    auxiliary_context,
)
from tests.runtime.helpers import (
    ClaimGate,
    LifecyclePlatform,
    rest_client_over,
)


class TestAuxiliaryClaimShutdown:
    """Shutdown keeps priority over a claiming turn and honours its grace."""

    @pytest.mark.parametrize("graceful", [False, True])
    async def test_auxiliary_claim_preserves_shutdown_priority_and_grace(
        self,
        graceful: bool,
    ) -> None:
        peer = LifecyclePlatform()
        peer.add_message("trigger")
        gate = ClaimGate(peer, "aux", suppress_cancel=True)
        downstream: list[str] = []
        async with rest_client_over(gate.answer) as rest:

            async def handler(ctx: ExecutionContext, event: Any) -> None:
                await ctx.claim_message("aux")
                downstream.append(event.payload.id)

            ctx = auxiliary_context(
                rest, handler, max_cycle_seconds=60, execution_type=ClosingExecution
            )
            assert isinstance(ctx, ClosingExecution)
            ctx.waiting_for_idle = asyncio.Event()
            await ctx.start()
            await gate.entered.wait()
            shutdown = asyncio.create_task(ctx.stop(timeout=60 if graceful else None))
            if graceful:
                await ctx.waiting_for_idle.wait()
            else:
                await gate.cancelled.wait()
            gate.release.set()
            assert await shutdown
            assert downstream == (["trigger"] if graceful else [])
            assert not ctx.is_running
