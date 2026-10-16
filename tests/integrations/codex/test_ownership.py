"""Startup and teardown retain ownership until helper and tree are dead."""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path

import pytest

from band.core.exceptions import BandConnectionError
from band.integrations.codex import stdio_client, winspawn
from band.integrations.codex.winspawn import JobNotDrained
from tests.integrations.codex.lifecycle import WindowsLifecycle


class TestWindowsProcessOwnership:
    async def test_closed_process_remains_observable_after_job_release(
        self, windows_lifecycle: WindowsLifecycle
    ) -> None:
        peer = windows_lifecycle
        peer.status.set_result(winspawn._READY)
        await peer.client.connect()
        await peer.client.close()
        assert peer.client._proc is not None
        assert peer.client._proc.returncode is not None
        assert peer.client._windows_process is None

    @pytest.mark.parametrize("cancel", [False, True], ids=["failed-status", "cancel"])
    async def test_failed_startup_does_not_close_a_reused_status_writer(
        self,
        windows_lifecycle: WindowsLifecycle,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        cancel: bool,
    ) -> None:
        peer = windows_lifecycle
        status_pipe = winspawn._status_pipe
        writers: list[int] = []

        def tracked_pipe() -> tuple[int, int]:
            read_fd, write_fd = status_pipe()
            writers.append(write_fd)
            return read_fd, write_fd

        monkeypatch.setattr(winspawn, "_status_pipe", tracked_pipe)
        connecting = asyncio.create_task(peer.client.connect())
        await peer.status_started.wait()
        [writer] = writers
        with (tmp_path / "replacement").open("w+b") as replacement:
            os.dup2(replacement.fileno(), writer)
            try:
                if cancel:
                    connecting.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await connecting
                else:
                    peer.status.set_exception(winspawn.SpawnFailure(None))
                    with pytest.raises(BandConnectionError):
                        await connecting
                os.write(writer, b"still open")
                replacement.seek(0)
                assert replacement.read() == b"still open"
            finally:
                if writer != replacement.fileno():
                    with suppress(OSError):
                        os.close(writer)

    @pytest.mark.parametrize("members", [0, 1], ids=["empty-job", "child-created"])
    async def test_failed_spawn_reaps_helper_and_tree(
        self, windows_lifecycle: WindowsLifecycle, members: int
    ) -> None:
        peer = windows_lifecycle
        peer.job.active = members
        peer.status.set_result(f"{winspawn._STATUS_ERROR} 5")
        with pytest.raises(BandConnectionError):
            await peer.client.connect()
        assert peer.helper.returncode is not None
        assert peer.job.closed
        assert peer.client._windows_process is None

    async def test_cancel_during_creation_keeps_the_eventual_helper_owned(
        self, windows_lifecycle: WindowsLifecycle
    ) -> None:
        peer = windows_lifecycle
        peer.creation_allowed.clear()
        connecting = asyncio.create_task(peer.client.connect())
        await peer.creation_started.wait()
        connecting.cancel()
        peer.creation_allowed.set()
        with pytest.raises(asyncio.CancelledError):
            await connecting
        assert peer.helper.returncode is not None
        assert peer.job.closed

    @pytest.mark.parametrize("startup_failed", [False, True])
    async def test_drain_timeout_retries_the_same_job(
        self, windows_lifecycle: WindowsLifecycle, startup_failed: bool
    ) -> None:
        peer = windows_lifecycle
        peer.job.active = 1
        peer.job.drains = False
        peer.status.set_result(
            f"{winspawn._STATUS_ERROR} 5" if startup_failed else winspawn._READY
        )
        if startup_failed:
            with pytest.raises(JobNotDrained):
                await peer.client.connect()
        else:
            await peer.client.connect()
            with pytest.raises(JobNotDrained):
                await peer.client.close()
        owner = peer.client._windows_process
        assert owner is not None and owner.job is peer.job
        assert not peer.job.closed
        peer.job.drains = True
        await peer.client.close()
        assert peer.job.terminations == 2
        assert peer.job.closed
        assert peer.client._windows_process is None

    async def test_live_helper_prevents_empty_job_release(
        self, windows_lifecycle: WindowsLifecycle, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        peer = windows_lifecycle
        peer.status.set_result(winspawn._READY)
        await peer.client.connect()
        peer.helper.exits_on_kill = False
        monkeypatch.setattr(stdio_client, "_EXIT_OBSERVE_S", 0)
        with pytest.raises(TimeoutError, match="helper did not exit"):
            await peer.client.close()
        assert peer.client._windows_process is not None
        assert not peer.job.closed
        peer.helper.exits_on_kill = True
        await peer.client.close()
        assert peer.job.closed

    async def test_pending_creation_is_retained_for_retry(
        self, windows_lifecycle: WindowsLifecycle, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        peer = windows_lifecycle
        peer.creation_allowed.clear()
        monkeypatch.setattr(stdio_client, "_EXIT_OBSERVE_S", 0)
        connecting = asyncio.create_task(peer.client.connect())
        await peer.creation_started.wait()
        connecting.cancel()
        with pytest.raises(TimeoutError, match="creation is still pending"):
            await connecting
        owner = peer.client._windows_process
        assert owner is not None and owner.job is peer.job
        assert peer.client._proc is None
        assert not peer.job.closed
        peer.creation_allowed.set()
        monkeypatch.setattr(stdio_client, "_EXIT_OBSERVE_S", 2)
        await peer.client.close()
        assert peer.helper.returncode is not None
        assert peer.job.closed

    async def test_close_settles_readiness_before_releasing_its_descriptor(
        self, windows_lifecycle: WindowsLifecycle
    ) -> None:
        peer = windows_lifecycle
        connecting = asyncio.create_task(peer.client.connect())
        await peer.status_started.wait()
        read_fd = peer.read_fd
        assert read_fd is not None
        await peer.client.close()
        with pytest.raises((BandConnectionError, asyncio.CancelledError)):
            await connecting
        assert peer.status.cancelled()
        with pytest.raises(OSError):
            os.fstat(read_fd)
        assert not peer.client._connected
        assert peer.job.closed

    async def test_ready_continuation_cannot_publish_after_close_begins(
        self, windows_lifecycle: WindowsLifecycle
    ) -> None:
        peer = windows_lifecycle
        connecting = asyncio.create_task(peer.client.connect())
        await peer.status_started.wait()
        peer.status.set_result(winspawn._READY)
        closing = asyncio.create_task(peer.client.close())
        await closing
        with pytest.raises(BandConnectionError):
            await connecting
        assert not peer.client._connected
        assert peer.client._proc is None

    async def test_overlapping_connect_cannot_replace_startup_ownership(
        self, windows_lifecycle: WindowsLifecycle
    ) -> None:
        peer = windows_lifecycle
        connecting = asyncio.create_task(peer.client.connect())
        await peer.status_started.wait()
        owner = peer.client._windows_process
        with pytest.raises(BandConnectionError, match="already starting"):
            await peer.client.connect()
        assert peer.client._windows_process is owner
        peer.status.set_result(winspawn._READY)
        await connecting

    async def test_reconnect_cannot_replace_a_job_that_did_not_drain(
        self, windows_lifecycle: WindowsLifecycle
    ) -> None:
        peer = windows_lifecycle
        peer.status.set_result(winspawn._READY)
        await peer.client.connect()
        peer.job.active = 1
        peer.job.drains = False
        owner = peer.client._windows_process
        with pytest.raises(JobNotDrained):
            await peer.client.close()
        with pytest.raises(JobNotDrained):
            await peer.client.connect()
        assert peer.client._windows_process is owner
        assert not peer.job.closed
