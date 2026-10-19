"""A probe keeps partial spawns and failed cleanup reachable through the adapter."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from band.core.exceptions import CleanupIncompleteError
from tests.adapters.codex.kit import ROOM_ID, send_bootstrap
from tests.adapters.codex.probes import ProbeHost
from tests.adapters.waiting import spin


class TestProbeOwnership:
    async def test_one_second_probe_reaps_a_child_hung_before_handshake(
        self, probe_host: ProbeHost
    ) -> None:
        adapter = probe_host.adapter(startup_hang=True)

        result = await adapter.preflight(timeout_s=1)

        assert not result.ok
        probe_host.assert_exited()

    async def test_one_second_probe_reaps_a_late_published_hung_child_on_cancel(
        self, probe_host: ProbeHost, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = probe_host.adapter(startup_hang=True)
        create_process = asyncio.create_subprocess_exec
        created = asyncio.Event()
        return_process = asyncio.Event()
        processes: list[asyncio.subprocess.Process] = []

        async def late_creation(
            *args: str, **kwargs: Any
        ) -> asyncio.subprocess.Process:
            process = await create_process(*args, **kwargs)
            processes.append(process)
            created.set()
            await return_process.wait()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", late_creation)
        probing = asyncio.create_task(adapter.preflight(timeout_s=1))
        try:
            await asyncio.wait_for(created.wait(), timeout=5)
            probing.cancel()
            await spin()
            return_process.set()
            with pytest.raises(asyncio.CancelledError) as stopped:
                await probing
            assert stopped.value.__cause__ is None
            assert processes
            assert all(process.returncode is not None for process in processes)
            assert all(client._windows_process is None for client in probe_host.clients)
            assert adapter._room_clients == {}
        finally:
            return_process.set()
            probing.cancel()
            await asyncio.gather(probing, return_exceptions=True)
            await adapter.cleanup_all()
            for process in processes:
                if process.returncode is None:
                    process.kill()
                await process.wait()

    async def test_cancel_during_late_subprocess_creation_reaps_the_eventual_child(
        self, probe_host: ProbeHost, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = probe_host.adapter()
        create_process = asyncio.create_subprocess_exec
        created = asyncio.Event()
        return_process = asyncio.Event()
        processes: list[asyncio.subprocess.Process] = []

        async def late_creation(
            *args: str, **kwargs: Any
        ) -> asyncio.subprocess.Process:
            process = await create_process(*args, **kwargs)
            processes.append(process)
            created.set()
            await return_process.wait()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", late_creation)
        probing = asyncio.create_task(adapter.preflight(timeout_s=5))
        try:
            await asyncio.wait_for(created.wait(), timeout=5)
            probing.cancel()
            await spin()
            return_process.set()
            with pytest.raises(asyncio.CancelledError):
                await probing
            await adapter.cleanup_all()
            assert processes
            assert all(process.returncode is not None for process in processes)
            assert adapter._room_clients == {}
        finally:
            return_process.set()
            probing.cancel()
            await asyncio.gather(probing, return_exceptions=True)
            await adapter.cleanup_all()
            for process in processes:
                if process.returncode is None:
                    process.kill()
                await process.wait()

    async def test_probe_failure_does_not_skip_existing_room_cleanup(
        self, probe_host: ProbeHost
    ) -> None:
        adapter = probe_host.adapter(replies={"account/read": {"hang": True}})
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content="/model")
        [room_client] = probe_host.clients
        assert ROOM_ID in adapter._room_clients
        probing = asyncio.create_task(adapter.preflight(timeout_s=5))
        try:
            await probe_host.received("account/read")
            probe_client = probe_host.clients[-1]
            probe_client.refuse_close = True
            probing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await probing
            with pytest.raises(CleanupIncompleteError):
                await adapter.cleanup_all()
            assert room_client._proc is not None
            assert room_client._proc.returncode is not None
            assert adapter._workspaces.rooms == ()
            assert probe_client._proc is not None
            assert probe_client._proc.returncode is None
            probe_client.refuse_close = False
            await adapter.cleanup_all()
            probe_host.assert_exited()
        finally:
            probing.cancel()
            await asyncio.gather(probing, return_exceptions=True)
