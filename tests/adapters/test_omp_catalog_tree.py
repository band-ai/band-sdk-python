"""Catalog success cannot release a launcher-owned descendant tree."""

from __future__ import annotations

import asyncio
import inspect
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Literal

import psutil
import pytest

from band.core.exceptions import CleanupIncompleteError
from band.integrations.ompcatalog import OmpModelListing
from tests.adapters.test_omp_catalog import catalog_adapter
from tests.integrations.ompcatalogtree import CatalogTreeGate


@pytest.mark.asyncio
class TestOmpCatalogTree:
    @pytest.mark.parametrize("late_child", [False, True])
    async def test_successful_catalog_reaps_children_after_launcher_exit(
        self, tmp_path: Path, monkeypatch, late_child: bool
    ) -> None:
        async with CatalogTreeGate() as gate:
            adapter, _receipts = catalog_adapter(
                tmp_path,
                "late-cli-tree" if late_child else "exited-cli-tree",
                env={"OMP_CATALOG_GATE_PORT": str(gate.port)},
            )
            connected = asyncio.Event()
            spawned_late = False
            original_close = OmpModelListing.close

            async def close(listing: OmpModelListing) -> None:
                nonlocal spawned_late
                if late_child and not spawned_late:
                    await connected.wait()
                    await gate.spawn_late_child()
                    spawned_late = True
                await original_close(listing)

            monkeypatch.setattr(OmpModelListing, "close", close)
            running = asyncio.create_task(adapter.probe_models(timeout_s=10))
            try:
                async with asyncio.timeout(15):
                    await gate.connected()
                    connected.set()
                    catalog = await running
                    assert catalog.models == ()
                    await adapter.cleanup_all()
                    assert len(gate.receipts) == (2 if late_child else 1)
                    gate.assert_settled()
                    assert not adapter._rooms
            finally:
                connected.set()
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
                await adapter.cleanup_all()

    @pytest.mark.skipif(
        os.name == "nt", reason="Windows custody uses Job handles, not group ids"
    )
    async def test_reused_launcher_pid_does_not_signal_an_unrelated_group(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        protected = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            "import threading; print('ready', flush=True); threading.Event().wait()",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        adapter, _receipts = catalog_adapter(tmp_path, "empty-cli")
        original_close = OmpModelListing.close

        async def close(listing: OmpModelListing) -> None:
            process = listing._process
            assert process is not None and process.returncode is not None
            # Emulate a reaped PID being reused by a live, unrelated process group.
            monkeypatch.setattr(process._transport, "get_pid", lambda: protected.pid)
            await original_close(listing)

        monkeypatch.setattr(OmpModelListing, "close", close)
        try:
            assert protected.stdout is not None
            async with asyncio.timeout(10):
                await protected.stdout.readline()
                catalog = await adapter.probe_models(timeout_s=5)
                await adapter.cleanup_all()
                assert catalog.models == ()
                assert protected.returncode is None
        finally:
            try:
                await adapter.cleanup_all()
            finally:
                if protected.returncode is None:
                    try:
                        protected.kill()
                    except ProcessLookupError:
                        pass
                await protected.wait()

    @pytest.mark.skipif(os.name == "nt", reason="Windows custody uses Job accounting")
    async def test_stale_process_cache_cannot_prove_group_exit(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        async with CatalogTreeGate() as gate:
            adapter, _receipts = catalog_adapter(
                tmp_path,
                "exited-cli-tree",
                env={"OMP_CATALOG_GATE_PORT": str(gate.port)},
            )
            configured = asyncio.Event()
            second_signal = asyncio.Event()
            release_signal = threading.Event()
            loop = asyncio.get_running_loop()
            original_close = OmpModelListing.close
            original_signal = os.killpg
            stopped = False

            async def close(listing: OmpModelListing) -> None:
                await configured.wait()
                await original_close(listing)

            def signal_group(group: int, sig: int) -> None:
                nonlocal stopped
                if not stopped:
                    stopped = True
                    original_signal(group, signal.SIGSTOP)
                    return
                loop.call_soon_threadsafe(second_signal.set)
                if not release_signal.wait(15):
                    raise RuntimeError("The test did not release native group exit")
                original_signal(group, sig)

            monkeypatch.setattr(OmpModelListing, "close", close)
            running = asyncio.create_task(adapter.probe_models(timeout_s=10))
            retrying = asyncio.create_task(second_signal.wait())
            try:
                async with asyncio.timeout(10):
                    await gate.connected()
                    observed = gate.receipts[0]
                    stale = psutil.Process(observed.pid)
                    monkeypatch.setattr(stale, "is_running", lambda: False)
                    monkeypatch.setattr(psutil, "process_iter", lambda: iter((stale,)))
                    monkeypatch.setattr(os, "killpg", signal_group)
                    configured.set()
                    await asyncio.wait(
                        (running, retrying), return_when=asyncio.FIRST_COMPLETED
                    )
                    assert (
                        psutil.Process(observed.pid).status() == psutil.STATUS_STOPPED
                    )
                    assert not running.done(), (
                        "A catalog probe returned before its stopped group member exited"
                    )
                    release_signal.set()
                    assert (await running).models == ()
                    gate.assert_settled()
            finally:
                configured.set()
                release_signal.set()
                running.cancel()
                retrying.cancel()
                await asyncio.gather(running, retrying, return_exceptions=True)
                await adapter.cleanup_all()

    @pytest.mark.skipif(os.name == "nt", reason="Windows custody uses Job accounting")
    @pytest.mark.parametrize("failure_location", ["leader", "group", "status"])
    async def test_unknown_group_inspection_keeps_cleanup_owned(
        self,
        tmp_path: Path,
        monkeypatch,
        failure_location: Literal["leader", "group", "status"],
    ) -> None:
        async with CatalogTreeGate() as gate:
            adapter, _receipts = catalog_adapter(
                tmp_path,
                "exited-cli-tree",
                env={"OMP_CATALOG_GATE_PORT": str(gate.port)},
            )
            configured = asyncio.Event()
            original_close = OmpModelListing.close
            original_signal = os.killpg
            original_kill = os.kill
            original_group = os.getpgid
            original_status = psutil.Process.status
            inspection_allowed = False
            root_pid = 0
            child_pid = 0

            async def close(listing: OmpModelListing) -> None:
                nonlocal root_pid
                assert listing._process is not None
                root_pid = listing._process.pid
                await configured.wait()
                await original_close(listing)

            def signal_group(group: int, sig: int) -> None:
                original_signal(group, sig if inspection_allowed else signal.SIGSTOP)

            def inspect_leader(pid: int, sig: int) -> None:
                if (
                    not inspection_allowed
                    and failure_location == "leader"
                    and pid == root_pid
                    and sig == 0
                ):
                    raise PermissionError("Native leader inspection is unavailable")
                original_kill(pid, sig)

            def inspect_group(pid: int) -> int:
                if (
                    not inspection_allowed
                    and failure_location == "group"
                    and pid == child_pid
                ):
                    raise PermissionError("Native group inspection is unavailable")
                return original_group(pid)

            def inspect_status(process: psutil.Process) -> str:
                if (
                    not inspection_allowed
                    and failure_location == "status"
                    and process.pid == child_pid
                ):
                    raise psutil.AccessDenied(process.pid)
                return original_status(process)

            monkeypatch.setattr(OmpModelListing, "close", close)
            running = asyncio.create_task(adapter.probe_models(timeout_s=10))
            try:
                async with asyncio.timeout(10):
                    await gate.connected()
                    observed = gate.receipts[0]
                    child_pid = observed.pid
                    monkeypatch.setattr(os, "killpg", signal_group)
                    monkeypatch.setattr(os, "kill", inspect_leader)
                    monkeypatch.setattr(os, "getpgid", inspect_group)
                    monkeypatch.setattr(psutil.Process, "status", inspect_status)
                    configured.set()
                    incomplete = False
                    try:
                        await running
                    except CleanupIncompleteError:
                        incomplete = True
                    physical = psutil.Process(child_pid)
                    assert physical.create_time() == observed.created
                    assert original_status(physical) != psutil.STATUS_ZOMBIE
                    assert incomplete, (
                        "A probe released a live group whose exit could not be inspected"
                    )
                    inspection_allowed = True
                    await adapter.cleanup_all()
                    gate.assert_settled()
            finally:
                inspection_allowed = True
                configured.set()
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
                await adapter.cleanup_all()

    @pytest.mark.skipif(os.name == "nt", reason="Windows custody uses Job accounting")
    @pytest.mark.parametrize("observation", ["missing-pid", "clock-adjustment"])
    async def test_retry_keeps_live_members_after_census_or_clock_changes(
        self,
        tmp_path: Path,
        monkeypatch,
        observation: Literal["missing-pid", "clock-adjustment"],
    ) -> None:
        async with CatalogTreeGate() as gate:
            adapter, _receipts = catalog_adapter(
                tmp_path,
                "exited-cli-tree",
                env={"OMP_CATALOG_GATE_PORT": str(gate.port)},
            )
            configured = asyncio.Event()
            retrying = asyncio.Event()
            release_signal = threading.Event()
            loop = asyncio.get_running_loop()
            original_close = OmpModelListing.close
            original_signal = os.killpg
            original_group = os.getpgid
            original_pids = psutil.pids
            process_class = type(psutil.Process()._proc)
            original_creation_time = process_class.create_time
            if (
                observation == "clock-adjustment"
                and "monotonic"
                not in inspect.signature(original_creation_time).parameters
            ):
                pytest.skip("The psutil backend has no separate wall-clock projection")
            inspection_allowed = False
            acknowledged = False
            child_pid = 0

            async def close(listing: OmpModelListing) -> None:
                await configured.wait()
                await original_close(listing)

            def signal_group(group: int, sig: int) -> None:
                nonlocal acknowledged
                if sig == 0:
                    original_signal(group, sig)
                    return
                if not inspection_allowed:
                    original_signal(group, signal.SIGSTOP)
                    return
                if not acknowledged:
                    acknowledged = True
                    original_signal(group, signal.SIGSTOP)
                    return
                loop.call_soon_threadsafe(retrying.set)
                if not release_signal.wait(15):
                    raise RuntimeError("The test did not release physical group exit")
                original_signal(group, sig)

            def inspect_group(pid: int) -> int:
                if not inspection_allowed and pid == child_pid:
                    raise PermissionError("Native group inspection is unavailable")
                return original_group(pid)

            def process_snapshot() -> list[int]:
                pids = original_pids()
                if (
                    observation == "missing-pid"
                    and acknowledged
                    and not release_signal.is_set()
                ):
                    return [pid for pid in pids if pid != child_pid]
                return pids

            def creation_time(process, monotonic: bool = False) -> float:
                value = original_creation_time(process, monotonic=monotonic)
                if observation == "clock-adjustment" and acknowledged and not monotonic:
                    return value + 3600
                return value

            monkeypatch.setattr(OmpModelListing, "close", close)
            running = asyncio.create_task(adapter.probe_models(timeout_s=10))
            cleanup: asyncio.Task[None] | None = None
            retried = asyncio.create_task(retrying.wait())
            try:
                async with asyncio.timeout(15):
                    await gate.connected()
                    observed = gate.receipts[0]
                    child_pid = observed.pid
                    physical = psutil.Process(child_pid)
                    monkeypatch.setattr(os, "killpg", signal_group)
                    monkeypatch.setattr(os, "getpgid", inspect_group)
                    monkeypatch.setattr(psutil, "pids", process_snapshot)
                    if observation == "clock-adjustment":
                        monkeypatch.setattr(process_class, "create_time", creation_time)
                    configured.set()
                    with pytest.raises(CleanupIncompleteError):
                        await running
                    assert (
                        physical.is_running()
                        and physical.status() != psutil.STATUS_ZOMBIE
                    )
                    inspection_allowed = True
                    cleanup = asyncio.create_task(adapter.cleanup_all())
                    await asyncio.wait(
                        (cleanup, retried), return_when=asyncio.FIRST_COMPLETED
                    )
                    assert (
                        physical.is_running()
                        and physical.status() != psutil.STATUS_ZOMBIE
                    )
                    if cleanup.done():
                        error = cleanup.exception()
                        assert isinstance(error, CleanupIncompleteError), (
                            f"Cleanup released live PID {observed.pid}; "
                            f"after {observation}; signal acknowledged: {acknowledged}"
                        )
                    release_signal.set()
                    monkeypatch.setattr(
                        process_class, "create_time", original_creation_time
                    )
                    if not cleanup.done():
                        await cleanup
                    await adapter.cleanup_all()
                    gate.assert_settled()
            finally:
                inspection_allowed = True
                configured.set()
                release_signal.set()
                monkeypatch.setattr(
                    process_class, "create_time", original_creation_time
                )
                running.cancel()
                retried.cancel()
                await asyncio.gather(running, retried, return_exceptions=True)
                if cleanup is not None:
                    await asyncio.gather(cleanup, return_exceptions=True)
                await adapter.cleanup_all()

    @pytest.mark.skipif(os.name == "nt", reason="Windows custody uses Job accounting")
    async def test_zombie_only_group_cleanup_can_retry_after_reaping(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        async with CatalogTreeGate() as gate:
            adapter, _receipts = catalog_adapter(
                tmp_path,
                "exited-cli-tree",
                env={"OMP_CATALOG_GATE_PORT": str(gate.port)},
            )
            entered = asyncio.Event()
            configured = asyncio.Event()
            original_close = OmpModelListing.close
            root: asyncio.subprocess.Process | None = None
            zombie_created = False

            async def close(listing: OmpModelListing) -> None:
                nonlocal root
                root = listing._process
                entered.set()
                await configured.wait()
                await original_close(listing)

            monkeypatch.setattr(OmpModelListing, "close", close)
            running = asyncio.create_task(adapter.probe_models(timeout_s=10))
            try:
                async with asyncio.timeout(10):
                    await gate.connected()
                    await entered.wait()
                    assert root is not None and root.returncode is not None
                    zombie = await gate.spawn_late_child(zombie=True)
                    zombie_created = True
                    physical = psutil.Process(zombie.pid)
                    assert physical.create_time() == zombie.created
                    assert physical.status() == psutil.STATUS_ZOMBIE
                    assert os.getpgid(gate.receipts[0].pid) != root.pid
                    configured.set()
                    try:
                        catalog = await running
                    except CleanupIncompleteError:
                        pass
                    else:
                        assert catalog.models == ()
                    await gate.reap_child()
                    await adapter.cleanup_all()
                    assert gate._alive(zombie) is None
            finally:
                configured.set()
                if zombie_created:
                    await gate.reap_child()
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
                await adapter.cleanup_all()
