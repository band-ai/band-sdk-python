"""Cleanup discovers descendants created by an owned helper after native stdin EOF."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.codex.winspawn import await_returncode, close_stdin
from tests.integrations.omprpc.cancellation import HeldCleanup
from tests.integrations.omprpc.kit import (
    ProcessGate,
    descendant_client,
    descendant_liveness,
    owned_rpc_process,
    process_is_alive,
    session_ids,
    unrelated_gate_child,
)


class TestEofDescendants:
    @pytest.mark.parametrize("stdio", ["inherit", "discard"])
    async def test_close_reaps_a_detached_child_created_after_stdin_eof(
        self,
        process_gate: ProcessGate,
        stdio: str,
    ) -> None:
        client = descendant_client(
            process_gate, scenario="eof-descendants", stdio=stdio, detached=True
        )
        cleanup: HeldCleanup | None = None
        closing: asyncio.Task[None] | None = None
        async with unrelated_gate_child(process_gate) as unrelated:
            try:
                await client.start()
                helper = await process_gate.connection()
                root = owned_rpc_process(client)
                cleanup = HeldCleanup(client)
                closing = asyncio.create_task(client.close())
                await cleanup.wait_for_join(closing)

                await close_stdin(root)
                late_child = await process_gate.connection()
                assert await await_returncode(root, float("inf"))
                assert late_child.receipt.parent_pid == helper.process.pid
                ids = session_ids([late_child.process.pid])
                if ids is not None:
                    assert ids == [late_child.process.pid]
                assert await descendant_liveness([helper, late_child]) == [True, True]
                assert await asyncio.to_thread(process_is_alive, unrelated.process)
                assert not closing.done()

                cleanup.release.set()
                await closing
                assert await descendant_liveness([helper, late_child]) == [False, False]
                assert await asyncio.to_thread(process_is_alive, unrelated.process)
            finally:
                if cleanup is not None:
                    cleanup.release.set()
                if closing is not None:
                    await asyncio.gather(closing, return_exceptions=True)
                await client.close()
