"""Cancelled cleanup reaps descendants without affecting unrelated processes."""

from __future__ import annotations

import asyncio

import psutil
import pytest

from band.integrations.omprpc import OmpProcessError
from band.integrations.omprpc.client import PROCESS_EXIT_TIMEOUT_S
from band.integrations.omprpc.scope import OmpProcessScope
from tests.integrations.omprpc.kit import (
    ClientFactory,
    DeferredSessionSignal,
    ProcessGate,
    RecycledEnvironment,
    RecycledGroup,
    connect_descendant_tree,
    crash_rpc_root,
    descendant_client,
    descendant_liveness,
    job_member_pids,
    owned_rpc_process,
    process_is_alive,
    refuse_containment,
    session_ids,
    unrelated_gate_child,
    wait_for_descendants,
)
from tests.ostable import skip_unless_os


class TestProcessTreeCleanup:
    @pytest.mark.parametrize("accepted_on", [("posix",)])
    async def test_reused_group_during_capture_cannot_adopt_an_unrelated_process(
        self,
        client_factory: ClientFactory,
        process_gate: ProcessGate,
        monkeypatch: pytest.MonkeyPatch,
        accepted_on: tuple[str, ...],
    ) -> None:
        skip_unless_os(accepted_on)
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

    async def test_close_reaps_detached_descendants_without_affecting_other_owners(
        self, process_gate: ProcessGate
    ) -> None:
        client = descendant_client(process_gate, detached=True)
        other = descendant_client(process_gate, detached=True)
        async with unrelated_gate_child(process_gate) as unrelated:
            try:
                await client.start()
                descendants = await wait_for_descendants(process_gate)
                root = owned_rpc_process(client)
                await other.start()
                other_descendants = await wait_for_descendants(process_gate)
                other_root = owned_rpc_process(other)

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

    async def test_crash_then_close_reaps_detached_descendants_without_affecting_other_owners(
        self, process_gate: ProcessGate
    ) -> None:
        client = descendant_client(process_gate, detached=True)
        other = descendant_client(process_gate, detached=True)
        async with unrelated_gate_child(process_gate) as unrelated:
            try:
                await client.start()
                descendants = await wait_for_descendants(process_gate)
                root = owned_rpc_process(client)
                await other.start()
                other_descendants = await wait_for_descendants(process_gate)
                other_root = owned_rpc_process(other)
                await crash_rpc_root(client)

                await client.close()
                assert root.returncode is not None
                assert await descendant_liveness(descendants) == [False, False]
                assert other_root.returncode is None
                assert await descendant_liveness(other_descendants) == [True, True]
                assert await asyncio.to_thread(process_is_alive, unrelated.process)
                await other.close()
                assert await descendant_liveness(other_descendants) == [False, False]
                assert await asyncio.to_thread(process_is_alive, unrelated.process)
            finally:
                await client.close()
                await other.close()

    @pytest.mark.parametrize("accepted_on", [("posix",)])
    async def test_detached_descendants_are_their_own_sessions(
        self, process_gate: ProcessGate, accepted_on: tuple[str, ...]
    ) -> None:
        skip_unless_os(accepted_on)
        client = descendant_client(process_gate, detached=True)
        try:
            await client.start()
            descendants = await wait_for_descendants(process_gate)
            root = owned_rpc_process(client)
            ids = session_ids([child.process.pid for child in descendants])
            assert ids == [child.process.pid for child in descendants]
            assert all(session != root.pid for session in ids)
        finally:
            await client.close()

    @pytest.mark.parametrize("accepted_on", [("nt",)])
    async def test_detached_descendants_are_job_members(
        self, process_gate: ProcessGate, accepted_on: tuple[str, ...]
    ) -> None:
        """Windows identity complementary to POSIX session ids, not a nested-job claim."""
        skip_unless_os(accepted_on)
        client = descendant_client(process_gate, detached=True)
        try:
            await client.start()
            descendants = await wait_for_descendants(process_gate)
            members = job_member_pids(client)
            assert {child.process.pid for child in descendants} <= members
        finally:
            await client.close()

    async def test_stale_matching_environment_cannot_adopt_a_reused_process_identity(
        self, process_gate: ProcessGate, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = descendant_client(process_gate, detached=True)
        async with unrelated_gate_child(process_gate) as unrelated:
            try:
                await client.start()
                descendants = await wait_for_descendants(process_gate)
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
        try:
            await client.start()
            descendants = await wait_for_descendants(process_gate)
            assert await descendant_liveness(descendants) == [True, True]
            await crash_rpc_root(client)
            await client.close()
            assert await descendant_liveness(descendants) == [False, False]
        finally:
            await client.close()

    async def test_failed_containment_cleanup_is_retried_before_restart(
        self, process_gate: ProcessGate, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = descendant_client(process_gate)
        try:
            await client.start()
            old_root = owned_rpc_process(client)
            descendants = await wait_for_descendants(process_gate)
            with monkeypatch.context() as patch:
                refuse_containment(patch)
                await crash_rpc_root(client)
                with pytest.raises(PermissionError, match="not signalable"):
                    await client.close()
                assert await descendant_liveness(descendants) == [True, True]

            await client.start()
            assert owned_rpc_process(client) is not old_root
            assert await descendant_liveness(descendants) == [False, False]
            await wait_for_descendants(process_gate)
            await client.close()
        finally:
            await client.close()

    @pytest.mark.parametrize("accepted_on", [("posix",)])
    @pytest.mark.parametrize(
        "hide_after_signal", [False, True], ids=["visible", "hidden-after-signal"]
    )
    async def test_unsettled_session_members_keep_close_retryable(
        self,
        process_gate: ProcessGate,
        monkeypatch: pytest.MonkeyPatch,
        accepted_on: tuple[str, ...],
        hide_after_signal: bool,
    ) -> None:
        skip_unless_os(accepted_on)
        client = descendant_client(process_gate)
        try:
            await client.start()
            old_root = owned_rpc_process(client)
            descendants = await wait_for_descendants(process_gate)
            boundary = DeferredSessionSignal(
                old_root.pid, descendants, hide_after_signal=hide_after_signal
            )
            with monkeypatch.context() as patch:
                boundary.install(patch)
                client.process_exit_timeout_s = 0
                await crash_rpc_root(client)
                for _ in range(2):
                    with pytest.raises(OmpProcessError, match="session members"):
                        await client.close()
                assert await descendant_liveness(descendants) == [True, True]
            client.process_exit_timeout_s = PROCESS_EXIT_TIMEOUT_S

            await client.start()
            assert owned_rpc_process(client) is not old_root
            assert await descendant_liveness(descendants) == [False, False]
            await wait_for_descendants(process_gate)
            await client.close()
        finally:
            await client.close()

    @pytest.mark.parametrize("stdio", ["inherit", "discard"])
    async def test_cancelled_startup_cleanup_reaps_descendants_but_not_unrelated_processes(
        self,
        process_gate: ProcessGate,
        stdio: str,
    ) -> None:
        tree = await connect_descendant_tree(
            process_gate, scenario="no-ready-descendants", stdio=stdio
        )
        async with unrelated_gate_child(process_gate) as unrelated:
            try:
                root_process = await asyncio.to_thread(psutil.Process, tree.root.pid)
                closing = tree.starting
                assert closing.cancel()
                await tree.cleanup.wait_for_join(closing)
                for _ in range(3):
                    await tree.cleanup.cancel_and_wait_for_rejoin(closing)
                    assert tree.root.returncode is None
                    assert await asyncio.to_thread(process_is_alive, root_process)
                    assert await descendant_liveness(tree.descendants) == [True, True]
                    assert await asyncio.to_thread(process_is_alive, unrelated.process)
                    assert not tree.cleanup.finished.is_set()

                tree.cleanup.release.set()
                async with asyncio.timeout(10):
                    with pytest.raises(asyncio.CancelledError):
                        await closing
                    await tree.root.wait()
                assert tree.cleanup.finished.is_set()
                assert tree.root.returncode is not None
                assert await descendant_liveness(tree.descendants) == [False, False]
                assert await asyncio.to_thread(process_is_alive, unrelated.process)
                await tree.client.close()
            finally:
                tree.cleanup.release.set()
                tree.starting.cancel()
                await asyncio.gather(tree.starting, return_exceptions=True)
                await tree.client.close()

    @pytest.mark.parametrize("stdio", ["inherit", "discard"])
    async def test_cancelled_close_cleanup_reaps_descendants_but_not_unrelated_processes(
        self,
        process_gate: ProcessGate,
        stdio: str,
    ) -> None:
        tree = await connect_descendant_tree(
            process_gate, scenario="descendants", stdio=stdio
        )
        closing: asyncio.Task[None] | None = None
        async with unrelated_gate_child(process_gate) as unrelated:
            try:
                async with asyncio.timeout(5):
                    await tree.starting
                root_process = await asyncio.to_thread(psutil.Process, tree.root.pid)
                closing = asyncio.create_task(tree.client.close())
                await tree.cleanup.wait_for_join(closing)
                await tree.cleanup.cancel_and_wait_for_rejoin(closing)
                for _ in range(3):
                    await tree.cleanup.cancel_and_wait_for_rejoin(closing)
                    assert tree.root.returncode is None
                    assert await asyncio.to_thread(process_is_alive, root_process)
                    assert await descendant_liveness(tree.descendants) == [True, True]
                    assert await asyncio.to_thread(process_is_alive, unrelated.process)
                    assert not tree.cleanup.finished.is_set()

                tree.cleanup.release.set()
                async with asyncio.timeout(10):
                    with pytest.raises(asyncio.CancelledError):
                        await closing
                    await tree.root.wait()
                assert tree.cleanup.finished.is_set()
                assert tree.root.returncode is not None
                assert await descendant_liveness(tree.descendants) == [False, False]
                assert await asyncio.to_thread(process_is_alive, unrelated.process)
                await tree.client.close()
            finally:
                tree.cleanup.release.set()
                tree.starting.cancel()
                tasks = [tree.starting, *([closing] if closing is not None else [])]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await tree.client.close()
