"""Expired process ownership must not signal an unrelated replacement."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import psutil
import pytest

from tests.integrations.omprpc.gate import kill_gate_processes
from tests.integrations.omprpc.pidkit import RetainedProcess, signal_scope
from tests.integrations.omprpc.processes import owned_rpc_process, peer_process
from tests.integrations.omprpc.spawn import ClientFactory
from tests.integrations.ompscenarios import PeerScenario


class TestExpiredProcesses:
    @pytest.mark.parametrize(
        "cleanup",
        [
            pytest.param(signal_scope, id="scope"),
            pytest.param(kill_gate_processes, id="gate"),
        ],
    )
    async def test_a_replacement_remains_usable_after_expired_process_cleanup(
        self,
        client_factory: ClientFactory,
        cleanup: Callable[[list[psutil.Process]], None],
    ) -> None:
        original = await client_factory(PeerScenario.STANDARD_TURN)
        expired = RetainedProcess(owned_rpc_process(original).pid)
        await original.close()
        assert not await asyncio.to_thread(expired.is_running)

        replacement = await client_factory(PeerScenario.STANDARD_TURN)
        expired.replacement = await peer_process(replacement)
        await asyncio.to_thread(cleanup, [expired])

        async with asyncio.timeout(10):
            assert (await replacement.request("get_state"))["isSettled"] is True
