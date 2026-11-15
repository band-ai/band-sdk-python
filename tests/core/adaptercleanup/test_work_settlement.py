"""Actual accepted turns keep their resource through cancellation finalizers."""

from __future__ import annotations

import pytest

from band.core.exceptions import CleanupIncompleteError
from band.core.turn_lifecycle import CYCLE_CANCEL_GRACE_SECONDS
from tests.adaptercleanup import RESOURCE_PATH, HostKind, resource_peer
from tests.core.adaptercleanup.work import (
    SuppressingWorkingAdapter,
    WorkingAdapter,
    accepted_work,
    observe_shutdown,
)
from tests.runtime.helpers import ROOM_ID
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock


class TestWorkSettlement:
    @pytest.mark.parametrize("kind", list(HostKind))
    @pytest.mark.parametrize(
        "adapter_type",
        [WorkingAdapter, SuppressingWorkingAdapter],
        ids=["propagates-cancellation", "suppresses-cancellation"],
    )
    async def test_finalizer_keeps_resource_until_accepted_work_settles(
        self, kind: HostKind, adapter_type: type[WorkingAdapter]
    ) -> None:
        async with (
            resource_peer() as client,
            accepted_work(client, kind, adapter_type) as work,
        ):
            stopping = work.start_stop()
            work.cancel_oneshot()
            await work.adapter.finalizing.wait()
            assert not stopping.done()
            assert (await work.client.get(RESOURCE_PATH)).status_code == 200

            work.adapter.release.set()
            await stopping
            assert work.adapter.finished.is_set()
            assert work.client.is_closed

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_stop_retains_finalizer_whose_grace_expires_during_shutdown(
        self,
    ) -> None:
        async with (
            resource_peer() as client,
            controlled_clock() as clock,
            accepted_work(client, HostKind.AGENT) as work,
        ):
            execution = work.execution
            scope = execution.current_scope
            assert scope is not None and scope.handler is not None
            task = scope.handler.task
            execution.interrupt()
            await work.adapter.finalizing.wait()
            remaining_grace = CYCLE_CANCEL_GRACE_SECONDS / 2
            await clock.elapse(remaining_grace)

            with observe_shutdown(scope) as shutdown:
                stopping = work.start_stop()
                await shutdown.wait()
                await clock.elapse(remaining_grace)
                with pytest.raises(ExceptionGroup) as raised:
                    await stopping

            assert raised.value.subgroup(CleanupIncompleteError) is not None
            assert task.cancelling() == 1
            assert not task.done()
            assert not work.adapter.finished.is_set()
            assert work.runtime.unsettled_rooms == [ROOM_ID]
            assert not work.client.is_closed
            assert (await work.client.get(RESOURCE_PATH)).status_code == 200

        assert work.adapter.finished.is_set()
        assert work.client.is_closed
