"""Cancelled cleanup reaps descendants without affecting unrelated processes."""

from __future__ import annotations

import asyncio
import os

import psutil
import pytest
from pydantic import JsonValue

from band.integrations import omprpc
from band.integrations.codex.winspawn import Job
from band.integrations.omprpc import OmpProcessError
from tests.integrations.omprpc.cancellation import HeldCleanup
from tests.integrations.omprpc.kit import (
    DeferredSessionSignal,
    ProcessGate,
    RecycledEnvironment,
    descendant_client,
    descendant_liveness,
    process_is_alive,
    refuse_process_signal,
    unrelated_gate_child,
)


class TestProcessTreeCleanup:
    @pytest.mark.skipif(os.name == "nt", reason="POSIX setsid ownership boundary")
    @pytest.mark.parametrize("root_exit", ["close", "crash"])
    async def test_close_reaps_detached_descendants_without_affecting_other_owners(
        self, process_gate: ProcessGate, root_exit: str
    ) -> None:
        client = descendant_client(process_gate, detached=True)
        other = descendant_client(process_gate, detached=True)
        crashing: asyncio.Task[dict[str, JsonValue]] | None = None
        async with unrelated_gate_child(process_gate) as unrelated:
            try:
                await client.start()
                descendants = [
                    await process_gate.connection(),
                    await process_gate.connection(),
                ]
                root = client._proc
                assert root is not None
                assert all(
                    os.getsid(child.process.pid) == child.process.pid
                    for child in descendants
                )
                assert all(
                    os.getsid(child.process.pid) != root.pid for child in descendants
                )
                await other.start()
                other_descendants = [
                    await process_gate.connection(),
                    await process_gate.connection(),
                ]
                other_root = other._proc
                assert other_root is not None
                if root_exit == "crash":
                    crashing = asyncio.create_task(client.request("crash"))
                    async with asyncio.timeout(5):
                        await root.wait()
                    assert root.returncode == 3
                    with pytest.raises(OmpProcessError):
                        await crashing

                await client.close()
                assert root.returncode is not None
                assert await descendant_liveness(descendants) == [False, False]
                assert other_root.returncode is None
                assert await descendant_liveness(other_descendants) == [True, True]
                assert await asyncio.to_thread(process_is_alive, unrelated.process)
                assert (await other.request("get_state"))["isSettled"] is True
                await other.close()
                assert await descendant_liveness(other_descendants) == [False, False]
                assert await asyncio.to_thread(process_is_alive, unrelated.process)
            finally:
                await client.close()
                await other.close()
                if crashing is not None:
                    await asyncio.gather(crashing, return_exceptions=True)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX inherited-scope boundary")
    async def test_stale_matching_environment_cannot_adopt_a_reused_process_identity(
        self, process_gate: ProcessGate, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = descendant_client(process_gate, detached=True)
        async with unrelated_gate_child(process_gate) as unrelated:
            try:
                await client.start()
                descendants = [
                    await process_gate.connection(),
                    await process_gate.connection(),
                ]
                root = client._proc
                assert root is not None
                boundary = RecycledEnvironment(unrelated, root.pid)
                with monkeypatch.context() as patch:
                    boundary.install(patch)
                    await client.close()
                assert boundary.recycled
                assert await descendant_liveness(descendants) == [False, False]
                assert await asyncio.to_thread(process_is_alive, unrelated.process)
            finally:
                await client.close()

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
                boundary.install(patch)
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
        monkeypatch: pytest.MonkeyPatch,
        stdio: str,
        interruption: str,
    ) -> None:
        client = descendant_client(
            process_gate,
            scenario=(
                "no-ready-descendants" if interruption == "startup" else "descendants"
            ),
            stdio=stdio,
        )
        cleanup = HeldCleanup(client, monkeypatch)
        starting: asyncio.Task[None] | None = None
        closing: asyncio.Task[None] | None = None
        async with unrelated_gate_child(process_gate) as unrelated:
            try:
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
                        "Peer did not connect its descendants: "
                        f"startup_done={starting.done()}, "
                        f"root_pid={process.pid if process else None}, "
                        f"returncode={process.returncode if process else None}"
                    )
                spawning = client._spawn_task
                assert spawning is not None
                async with asyncio.timeout(5):
                    root = await asyncio.shield(spawning)
                assert client._proc is root
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
                scope = client._process_scope
                owner = client._windows_process
                assert scope is not None or owner is not None
                if interruption == "startup":
                    closing = starting
                    assert closing.cancel()
                else:
                    async with asyncio.timeout(5):
                        await starting
                    closing = asyncio.create_task(client.close())
                await cleanup.wait_for_join(closing)
                if interruption == "close":
                    await cleanup.cancel_and_wait_for_rejoin(closing)

                for _ in range(3):
                    await cleanup.cancel_and_wait_for_rejoin(closing)
                    assert client._proc is root
                    assert client._process_scope is scope
                    assert client._windows_process is owner
                    assert root.returncode is None
                    assert await asyncio.to_thread(process_is_alive, root_process)
                    assert await descendant_liveness(descendants) == [True, True]
                    assert await asyncio.to_thread(process_is_alive, unrelated.process)
                    assert not cleanup.finished.is_set()
                assert closing.cancelling() == 4

                cleanup.release.set()
                async with asyncio.timeout(10):
                    with pytest.raises(asyncio.CancelledError):
                        await closing
                    await root.wait()
                assert cleanup.finished.is_set()
                assert root.returncode is not None
                assert await descendant_liveness(descendants) == [False, False]
                assert await asyncio.to_thread(process_is_alive, unrelated.process)
                await client.close()
            finally:
                cleanup.release.set()
                tasks = {task for task in (starting, closing) if task is not None}
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await client.close()
