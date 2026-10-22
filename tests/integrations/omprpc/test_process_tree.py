"""Cancelled cleanup reaps descendants without affecting unrelated processes."""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import suppress

import psutil
import pytest
from pydantic import JsonValue

from band.integrations import omprpc
from band.integrations.codex.winspawn import Job
from band.integrations.omprpc import OmpProcessError, OmpRpcClient
from tests.integrations.omprpc.kit import (
    OMP_PEER_SCRIPT,
    DeferredSessionSignal,
    ProcessGate,
    descendant_client,
    descendant_liveness,
    process_is_alive,
    refuse_process_signal,
)


class TestProcessTreeCleanup:
    async def test_close_reaps_owned_descendants_after_the_root_has_crashed(
        self, process_gate: ProcessGate
    ) -> None:
        client = descendant_client(process_gate)
        crashing: asyncio.Task[dict[str, JsonValue]] | None = None
        try:
            await client.start()
            descendants = [
                await process_gate.connection(),
                await process_gate.connection(),
            ]
            root = client._proc
            assert root is not None
            assert await descendant_liveness(descendants) == [True, True]

            crashing = asyncio.create_task(client.request("crash"))
            async with asyncio.timeout(5):
                await root.wait()
            assert root.returncode == 3
            with pytest.raises(OmpProcessError):
                await crashing
            await client.close()

            assert await descendant_liveness(descendants) == [False, False]
            await client.close()
        finally:
            await client.close()
            if crashing is not None:
                await asyncio.gather(crashing, return_exceptions=True)

    async def test_failed_containment_cleanup_is_retried_before_restart(
        self, process_gate: ProcessGate, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = descendant_client(process_gate)
        crashing: asyncio.Task[dict[str, JsonValue]] | None = None
        try:
            await client.start()
            old_root = client._proc
            assert old_root is not None
            descendants = [
                await process_gate.connection(),
                await process_gate.connection(),
            ]
            with monkeypatch.context() as patch:
                if os.name == "nt":
                    patch.setattr(Job, "terminate", refuse_process_signal)
                else:
                    patch.setattr(os, "killpg", refuse_process_signal)
                crashing = asyncio.create_task(client.request("crash"))
                async with asyncio.timeout(5):
                    await old_root.wait()
                with pytest.raises(PermissionError, match="not signalable"):
                    await client.close()
                assert await descendant_liveness(descendants) == [True, True]

            await client.start()
            assert client._proc is not old_root
            assert await descendant_liveness(descendants) == [False, False]
            await process_gate.connection()
            await process_gate.connection()
            await client.close()
        finally:
            await client.close()
            if crashing is not None:
                await asyncio.gather(crashing, return_exceptions=True)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX group settlement boundary")
    @pytest.mark.parametrize(
        "hide_after_signal", [False, True], ids=["visible", "hidden-after-signal"]
    )
    async def test_unsettled_session_members_keep_close_retryable(
        self,
        process_gate: ProcessGate,
        monkeypatch: pytest.MonkeyPatch,
        hide_after_signal: bool,
    ) -> None:
        client = descendant_client(process_gate)
        crashing: asyncio.Task[dict[str, JsonValue]] | None = None
        try:
            await client.start()
            old_root = client._proc
            assert old_root is not None
            descendants = [
                await process_gate.connection(),
                await process_gate.connection(),
            ]
            boundary = DeferredSessionSignal(
                old_root.pid, descendants, hide_after_signal=hide_after_signal
            )
            with monkeypatch.context() as patch:
                patch.setattr(os, "killpg", boundary.send)
                patch.setattr(os, "getpgid", boundary.group)
                patch.setattr(omprpc, "_PROCESS_EXIT_TIMEOUT_S", 0)
                crashing = asyncio.create_task(client.request("crash"))
                async with asyncio.timeout(5):
                    await old_root.wait()
                for _ in range(2):
                    with pytest.raises(OmpProcessError, match="session members"):
                        await client.close()
                assert await descendant_liveness(descendants) == [True, True]

            await client.start()
            assert client._proc is not old_root
            assert await descendant_liveness(descendants) == [False, False]
            await process_gate.connection()
            await process_gate.connection()
            await client.close()
        finally:
            await client.close()
            if crashing is not None:
                await asyncio.gather(crashing, return_exceptions=True)

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
