"""Actual accepted turns keep their resource through cancellation finalizers."""

from __future__ import annotations

import asyncio

import pytest

from band.agent import Agent
from band.core.exceptions import CleanupIncompleteError
from band.core.turn_lifecycle import CYCLE_CANCEL_GRACE_SECONDS
from band.runtime.execution import ExecutionContext
from band.runtime.oneshot import OneShotInvoker
from tests.adaptercleanup import HostKind, host_for, resource_peer
from tests.adapters.waiting import spin
from tests.conftest import make_message_event
from tests.core.adaptercleanup.work import SuppressingWorkingAdapter, WorkingAdapter
from tests.runtime.helpers import ROOM_ID
from tests.runtime.oneshot.kit import msg_body
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
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
        async with resource_peer() as client:
            adapter = adapter_type(client)
            adapter.add_cleanup(client.aclose)
            async with host_for(adapter, kind) as host:
                await host.start()
                host.platform.add_message("work")
                operation = None
                if isinstance(host.owner, OneShotInvoker):
                    operation = asyncio.create_task(
                        host.owner.handle_event(
                            msg_body(msg_id="work", room_id=ROOM_ID)
                        )
                    )
                else:
                    assert isinstance(host.owner, Agent)
                    runtime = host.owner._runtime.runtime
                    await runtime._on_room_joined(ROOM_ID, {})
                    await runtime._on_room_event(
                        ROOM_ID, make_message_event(room_id=ROOM_ID, msg_id="work")
                    )
                await adapter.entered.wait()
                stopping = asyncio.create_task(host.stop())
                if operation is not None:
                    operation.cancel()
                try:
                    await adapter.finalizing.wait()
                    assert not stopping.done()
                    assert (await client.get("/resource")).status_code == 200
                finally:
                    adapter.release.set()
                    if operation is not None:
                        await asyncio.gather(operation, return_exceptions=True)
                    await stopping
                assert adapter.finished.is_set()
                assert client.is_closed

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_stop_retains_finalizer_whose_grace_expires_during_shutdown(
        self,
    ) -> None:
        async with resource_peer() as client:
            adapter = WorkingAdapter(client)
            adapter.add_cleanup(client.aclose)
            async with (
                controlled_clock() as clock,
                host_for(adapter, HostKind.AGENT) as host,
            ):
                await host.start()
                assert isinstance(host.owner, Agent)
                runtime = host.owner._runtime.runtime
                host.platform.add_message("work")
                await runtime._on_room_joined(ROOM_ID, {})
                await runtime._on_room_event(
                    ROOM_ID, make_message_event(room_id=ROOM_ID, msg_id="work")
                )
                await adapter.entered.wait()
                execution = runtime.executions[ROOM_ID]
                assert isinstance(execution, ExecutionContext)
                scope = execution.current_scope
                assert scope is not None and scope.handler is not None
                work = scope.handler.task
                execution.interrupt()
                await adapter.finalizing.wait()
                remaining_grace = CYCLE_CANCEL_GRACE_SECONDS / 2
                await clock.elapse(remaining_grace)

                async with background(host.stop()) as stopping:
                    try:
                        await spin()
                        assert scope.shutdown
                        await clock.elapse(remaining_grace)
                        with pytest.raises(ExceptionGroup) as raised:
                            await stopping
                        assert raised.value.subgroup(CleanupIncompleteError) is not None
                        assert work.cancelling() == 1
                        assert not work.done()
                        assert not adapter.finished.is_set()
                        assert runtime.unsettled_rooms == [ROOM_ID]
                        assert not client.is_closed
                        assert (await client.get("/resource")).status_code == 200
                    finally:
                        adapter.release.set()
                        await asyncio.gather(work, stopping, return_exceptions=True)
                        await host.stop()

                assert adapter.finished.is_set()
                assert client.is_closed
