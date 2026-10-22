"""Cancelled cleanup reaps descendants without affecting unrelated processes."""

from __future__ import annotations

import asyncio
import os

import psutil
import pytest
from pydantic import JsonValue

from band.integrations.omprpc import OmpProcessError
from band.integrations.omprpc.client import PROCESS_EXIT_TIMEOUT_S
from band.integrations.omprpc.scope import OmpProcessScope
from tests.integrations.omppeer import CRASH_EXIT
from tests.integrations.omprpc.cancellation import HeldCleanup
from tests.integrations.omprpc.kit import (
    ClientFactory,
    DeferredSessionSignal,
    ProcessGate,
    RecycledEnvironment,
    RecycledGroup,
    descendant_client,
    descendant_liveness,
    owned_rpc_process,
    process_is_alive,
    refuse_containment,
    session_ids,
    unrelated_gate_child,
)


class TestProcessTreeCleanup:
    @pytest.mark.skipif(os.name == "nt", reason="POSIX process-group identity boundary")
    async def test_reused_group_during_capture_cannot_adopt_an_unrelated_process(
        self,
        client_factory: ClientFactory,
        process_gate: ProcessGate,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = await client_factory("basic")
        root = owned_rpc_process(client)
        await client.close()
        assert root.returncode is not None
        scope = OmpProcessScope()
        async with unrelated_gate_child(process_gate) as unrelated:
            boundary = RecycledGroup(root, unrelated)
            with monkeypatch.context() as patch:
                boundary.install(patch)
                scope.capture(root)
            assert boundary.reused
            assert unrelated.process not in scope.processes
            assert await asyncio.to_thread(process_is_alive, unrelated.process)

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
                root = owned_rpc_process(client)
                ids = session_ids([child.process.pid for child in descendants])
                if ids is not None:
                    assert ids == [child.process.pid for child in descendants]
                    assert all(session != root.pid for session in ids)
                await other.start()
                other_descendants = [
                    await process_gate.connection(),
                    await process_gate.connection(),
                ]
                other_root = owned_rpc_process(other)
                if root_exit == "crash":
                    crashing = asyncio.create_task(client.request("crash"))
                    async with asyncio.timeout(5):
                        await root.wait()
                    assert root.returncode == CRASH_EXIT
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
                root = owned_rpc_process(client)
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
            root = owned_rpc_process(client)
            assert await descendant_liveness(descendants) == [True, True]

            crashing = asyncio.create_task(client.request("crash"))
            async with asyncio.timeout(5):
                await root.wait()
            assert root.returncode == CRASH_EXIT
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
            old_root = owned_rpc_process(client)
            descendants = [
                await process_gate.connection(),
                await process_gate.connection(),
            ]
            with monkeypatch.context() as patch:
                refuse_containment(patch)
                crashing = asyncio.create_task(client.request("crash"))
                async with asyncio.timeout(5):
                    await old_root.wait()
                with pytest.raises(PermissionError, match="not signalable"):
                    await client.close()
                assert await descendant_liveness(descendants) == [True, True]

            await client.start()
            assert owned_rpc_process(client) is not old_root
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
            old_root = owned_rpc_process(client)
            descendants = [
                await process_gate.connection(),
                await process_gate.connection(),
            ]
            boundary = DeferredSessionSignal(
                old_root.pid, descendants, hide_after_signal=hide_after_signal
            )
            with monkeypatch.context() as patch:
                boundary.install(patch)
                client.process_exit_timeout_s = 0
                crashing = asyncio.create_task(client.request("crash"))
                async with asyncio.timeout(5):
                    await old_root.wait()
                for _ in range(2):
                    with pytest.raises(OmpProcessError, match="session members"):
                        await client.close()
                assert await descendant_liveness(descendants) == [True, True]
            client.process_exit_timeout_s = PROCESS_EXIT_TIMEOUT_S

            await client.start()
            assert owned_rpc_process(client) is not old_root
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
        client = descendant_client(
            process_gate,
            scenario=(
                "no-ready-descendants" if interruption == "startup" else "descendants"
            ),
            stdio=stdio,
        )
        cleanup = HeldCleanup(client)
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
                    process = getattr(client, "_proc", None)
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
                    assert root.returncode is None
                    assert await asyncio.to_thread(process_is_alive, root_process)
                    assert await descendant_liveness(descendants) == [True, True]
                    assert await asyncio.to_thread(process_is_alive, unrelated.process)
                    assert not cleanup.finished.is_set()

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
