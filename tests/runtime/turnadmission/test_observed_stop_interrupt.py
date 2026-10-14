"""An INTERRUPT consumes its turn even after a platform stop was observed."""

from __future__ import annotations

import pytest

from band.runtime.execution import ExecutionContext
from tests.runtime.conftest import wait_for_condition
from tests.runtime.helpers import LifecyclePlatform, synthetic_message
from tests.runtime.turnadmission.kit import (
    TurnRecorder,
    queued_synthetic,
    reconcile_once,
    started,
)


@pytest.mark.timeout(10)
class TestObservedStopInterrupt:
    """INTERRUPT outranks an observed platform stop: the message is not replayed."""

    async def test_interrupt_after_observed_stop_drops_synthetic_turn(
        self,
        ctx: ExecutionContext,
        platform: LifecyclePlatform,
        adapter: TurnRecorder,
    ) -> None:
        await started(ctx)
        adapter.release.clear()
        await ctx.on_event(synthetic_message("local-A"))
        await adapter.entered.wait()
        scope = ctx.current_scope
        assert scope is not None
        ctx.observe_platform_stop(scope)
        ctx.interrupt()

        await wait_for_condition(lambda: scope.settled)
        adapter.release.set()
        await reconcile_once(ctx, platform)
        assert adapter.completed == []
        assert queued_synthetic(ctx) == []
