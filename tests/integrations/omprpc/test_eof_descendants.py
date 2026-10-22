"""Cleanup discovers descendants created by an owned helper after native stdin EOF."""

from __future__ import annotations

import asyncio
import os
import threading

import pytest

from tests.integrations.omprpc.kit import (
    ProcessGate,
    descendant_client,
    descendant_liveness,
    process_is_alive,
    unrelated_gate_child,
)


class TestEofDescendants:
    @pytest.mark.skipif(os.name == "nt", reason="POSIX detached-helper boundary")
    @pytest.mark.parametrize("stdio", ["inherit", "discard"])
    async def test_close_reaps_a_detached_child_created_after_stdin_eof(
        self,
        process_gate: ProcessGate,
        monkeypatch: pytest.MonkeyPatch,
        stdio: str,
    ) -> None:
        client = descendant_client(
            process_gate, scenario="eof-descendants", stdio=stdio, detached=True
        )
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        closing: asyncio.Task[None] | None = None

        async with unrelated_gate_child(process_gate) as unrelated:
            try:
                await client.start()
                helper = await process_gate.connection()
                root = client._proc
                scope = client._process_scope
                assert root is not None and scope is not None
                signal = scope.signal

                def hold_signal() -> None:
                    loop.call_soon_threadsafe(entered.set)
                    if not release.wait(timeout=10):
                        raise TimeoutError(
                            "Test did not release the containment reaper"
                        )
                    signal()

                with monkeypatch.context() as patch:
                    patch.setattr(scope, "signal", hold_signal)
                    closing = asyncio.create_task(client.close())
                    try:
                        async with asyncio.timeout(10):
                            await entered.wait()
                            late_child = await process_gate.connection()
                            assert root.returncode is not None
                            assert late_child.receipt.parent_pid == helper.process.pid
                            assert (
                                os.getsid(late_child.process.pid)
                                == late_child.process.pid
                            )
                            assert await descendant_liveness([helper, late_child]) == [
                                True,
                                True,
                            ]
                            assert await asyncio.to_thread(
                                process_is_alive, unrelated.process
                            )
                            assert not closing.done()
                            release.set()
                            await closing
                    finally:
                        release.set()
                        await asyncio.gather(closing, return_exceptions=True)

                assert await descendant_liveness([helper, late_child]) == [False, False]
                assert await asyncio.to_thread(process_is_alive, unrelated.process)
            finally:
                release.set()
                if closing is not None:
                    await asyncio.gather(closing, return_exceptions=True)
                await client.close()
