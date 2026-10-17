"""Actual accepted turns keep their resource through cancellation finalizers."""

from __future__ import annotations

import asyncio

import pytest

from band.agent import Agent
from band.runtime.oneshot import OneShotInvoker
from tests.adaptercleanup import HostKind, host_for, resource_peer
from tests.conftest import make_message_event
from tests.core.adaptercleanup.work import SuppressingWorkingAdapter, WorkingAdapter
from tests.runtime.helpers import ROOM_ID
from tests.runtime.oneshot.kit import msg_body


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
