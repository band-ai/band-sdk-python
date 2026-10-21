"""Cancelled cleanup reaps descendants without affecting unrelated processes."""

from __future__ import annotations

import asyncio
import sys
from contextlib import suppress

import psutil
import pytest

from band.integrations.omprpc import OmpRpcClient
from tests.integrations.omprpc.kit import (
    OMP_PEER_SCRIPT,
    ProcessGate,
    process_is_alive,
)


class TestProcessTreeCleanup:
    @pytest.mark.parametrize("stdio", ["inherit", "discard"])
    @pytest.mark.parametrize("interruption", ["startup", "close"])
    async def test_cancelled_cleanup_reaps_descendants_but_not_unrelated_processes(
        self,
        process_gate: ProcessGate,
        stdio: str,
        interruption: str,
    ) -> None:
        peer = OMP_PEER_SCRIPT
        port = str(process_gate.port)
        unrelated = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            peer,
            "--scenario",
            "gated-child",
            "--gate-port",
            port,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        client = OmpRpcClient(
            (
                sys.executable,
                "-u",
                peer,
                "--scenario",
                "no-ready-descendants" if interruption == "startup" else "descendants",
                "--gate-port",
                port,
                "--child-stdio",
                stdio,
            )
        )
        starting: asyncio.Task[None] | None = None
        closing: asyncio.Task[None] | None = None
        cancellations: list[asyncio.TimerHandle] = []
        try:
            sibling = await process_gate.connection()
            unrelated_process = await asyncio.to_thread(psutil.Process, unrelated.pid)
            # Windows virtualenv launchers may remain between the parent and Python.
            assert sibling.process == unrelated_process or unrelated_process in (
                await asyncio.to_thread(sibling.process.parents)
            )
            starting = asyncio.create_task(client.start())
            try:
                descendants = [
                    await process_gate.connection(),
                    await process_gate.connection(),
                ]
            except TimeoutError:
                if starting.done():
                    await starting
                process = client._proc
                pytest.fail(
                    f"Peer did not connect its descendants: startup_done={starting.done()}, "
                    f"root_pid={process.pid if process else None}, "
                    f"returncode={process.returncode if process else None}"
                )
            root = client._proc
            assert root is not None
            root_process = await asyncio.to_thread(psutil.Process, root.pid)
            ancestors = [
                await asyncio.to_thread(process.process.parents)
                for process in descendants
            ]
            assert all(root_process in parents for parents in ancestors)
            assert (
                descendants[0].process in ancestors[1]
                or descendants[1].process in ancestors[0]
            )
            if interruption == "startup":
                closing = starting
            else:
                async with asyncio.timeout(5):
                    await starting
                closing = asyncio.create_task(client.close())
                await asyncio.sleep(0)
            closing.cancel()
            loop = asyncio.get_running_loop()
            cancellations = [
                loop.call_later(delay, closing.cancel) for delay in (0.01, 0.05, 0.1)
            ]
            async with asyncio.timeout(10):
                with pytest.raises(asyncio.CancelledError):
                    await closing
            assert root.returncode is not None
            for descendant in descendants:
                assert not await asyncio.to_thread(process_is_alive, descendant.process)
            assert await asyncio.to_thread(process_is_alive, sibling.process)
            assert unrelated.returncode is None
            await client.close()
        finally:
            for cancellation in cancellations:
                cancellation.cancel()
            tasks = {task for task in (starting, closing) if task is not None}
            for task in tasks:
                task.cancel()
            with suppress(ProcessLookupError):
                unrelated.kill()
            async with asyncio.timeout(5):
                await unrelated.wait()
            if client._proc is not None and client._proc.returncode is None:
                with suppress(ProcessLookupError):
                    client._proc.kill()
            await asyncio.gather(*tasks, return_exceptions=True)
