"""Expired process ownership must not signal an unrelated replacement."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import psutil
import pytest

from tests.integrations.omprpc.kit import (
    ClientFactory,
    kill_gate_processes,
    owned_rpc_process,
)
from tests.integrations.omprpc.pidkit import RetainedProcess, rpc_peer, signal_scope


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
        original = await client_factory("basic")
        expired = RetainedProcess(owned_rpc_process(original).pid)
        await original.close()
        assert not await asyncio.to_thread(expired.is_running)

        replacement = await client_factory("basic")
        expired.replacement = await rpc_peer(replacement)
        await asyncio.to_thread(cleanup, [expired])

        async with asyncio.timeout(10):
            assert (await replacement.request("get_state"))["isSettled"] is True
