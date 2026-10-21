"""Subprocess cleanup survives cancelled callers, retries a failed kill, and
stops everything the launched process spawned."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import textwrap
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

import pytest
import pytest_asyncio

from band.integrations.codex import CodexStdioClient, stdio_client
from band.integrations.codex.stdio_client import HELPER_PATH, kill_process_tree
from band.integrations.codex.winspawn import (
    Job,
    JobNotDrained,
    SpawnFailure,
    _read_status,
    _start_and_wait,
)
from tests.integrations.codex.cleanup import cleanup_snapshot
from tests.paths import ROOM_PEER

logger = logging.getLogger(__name__)

HEARTBEAT_S = 0.05
HEARTBEAT_FILE = "heartbeat"
QUIET_S = 0.5
# A forced close stays well inside this, yet a leaked process still ends on its own.
LEAKED_LIFETIME_S = 30
CLOSE_BOUND_S = 10

WORKER = textwrap.dedent(
    f"""
    import time
    for _ in range({int(LEAKED_LIFETIME_S / HEARTBEAT_S)}):
        with open({HEARTBEAT_FILE!r}, "a") as beat:
            beat.write(".")
        time.sleep({HEARTBEAT_S})
    """
)
# A launcher shim: it ignores stdin EOF, so close() must force-stop it, and its
# real work runs in a child that inherits the workdir.
SHIM = textwrap.dedent(
    f"""
    import subprocess, sys, time
    subprocess.Popen([sys.executable, "-c", {WORKER!r}])
    time.sleep({LEAKED_LIFETIME_S})
    """
)
# Written only if the launcher runs past stdin EOF. A tree kill that lands
# while it is blocked in that read never creates the file.
# The worker does not inherit stdin: on Windows a child that inherits the
# pipe its parent is blocked reading does not run until that read finishes.
STDIN_EOF_MARKER = "stdin-eof"
EXITING_SHIM = textwrap.dedent(
    f"""
    import subprocess, sys
    subprocess.Popen(
        [sys.executable, "-c", {WORKER!r}],
        stdin=subprocess.DEVNULL,
    )
    sys.stdin.read()
    open({STDIN_EOF_MARKER!r}, "w").close()
    """
)
# Starts the worker and exits. The worker keeps the workdir after this process is gone.
EXITED_LAUNCHER = textwrap.dedent(
    f"""
    import subprocess, sys
    subprocess.Popen([sys.executable, "-c", {WORKER!r}])
    """
)


@pytest_asyncio.fixture(loop_scope="function")
async def child(
    tmp_path: Path,
) -> AsyncIterator[tuple[CodexStdioClient, asyncio.subprocess.Process]]:
    client = CodexStdioClient(
        command=[
            sys.executable,
            "-u",
            str(ROOM_PEER),
            "--stay",
        ],
        cwd=str(tmp_path),
    )
    await client.connect()
    await client.initialize(client_name="test", client_title="test", client_version="1")
    process = client._proc
    assert process is not None
    try:
        yield client, process
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
        await client.close()


async def wait_for_heartbeat(path: Path) -> None:
    while not path.exists():
        await asyncio.sleep(HEARTBEAT_S)


async def wait_until_exited(process: asyncio.subprocess.Process) -> None:
    while process.returncode is None:
        await asyncio.sleep(HEARTBEAT_S)


async def assert_heartbeat_stopped(heartbeat: Path) -> None:
    stopped_at = heartbeat.stat().st_size
    await asyncio.sleep(QUIET_S)
    assert heartbeat.stat().st_size == stopped_at


class TestSubprocessCleanup:
    @pytest.mark.parametrize("cancel_caller", [False, True], ids=["returns", "cancel"])
    async def test_interrupted_close_still_reaps_the_child(
        self,
        child: tuple[CodexStdioClient, asyncio.subprocess.Process],
        cancel_caller: bool,
    ) -> None:
        client, process = child
        if cancel_caller:
            closing = asyncio.create_task(client.close())
            assert process.stdin is not None
            while not process.stdin.is_closing():
                await asyncio.sleep(0)
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
        else:
            await asyncio.wait_for(client.close(), timeout=CLOSE_BOUND_S)
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            logger.error(
                "Codex cleanup deadline: %s", cleanup_snapshot(client, process)
            )
            raise
        await client.close()
        assert process.returncode is not None

    async def test_failed_process_kill_can_be_retried(
        self,
        child: tuple[CodexStdioClient, asyncio.subprocess.Process],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client, process = child

        async def refuse_kill(_process: asyncio.subprocess.Process) -> None:
            raise RuntimeError("kill failed")

        def refuse_helper_kill() -> None:
            raise RuntimeError("kill failed")

        with monkeypatch.context() as patch:
            if client._windows_process is not None:
                patch.setattr(process, "kill", refuse_helper_kill)
            else:
                patch.setattr(stdio_client, "kill_process_tree", refuse_kill)
            with pytest.raises(RuntimeError, match="kill failed"):
                await client.close()
            assert process.returncode is None
        await client.close()
        assert process.returncode is not None

    async def test_overlapping_close_waits_for_the_same_child_to_exit(
        self, child: tuple[CodexStdioClient, asyncio.subprocess.Process]
    ) -> None:
        client, process = child
        first = asyncio.create_task(client.close())
        assert process.stdin is not None
        while not process.stdin.is_closing():
            await asyncio.sleep(0)
        await client.close()
        assert process.returncode is not None
        await first

    async def test_reconnect_reaps_the_previous_child_before_spawning(
        self, child: tuple[CodexStdioClient, asyncio.subprocess.Process]
    ) -> None:
        client, previous = child
        first = asyncio.create_task(client.close())
        assert previous.stdin is not None
        while not previous.stdin.is_closing():
            await asyncio.sleep(0)
        await client.connect()
        await first
        assert previous.returncode is not None
        assert client._proc is not previous
        await client.initialize(
            client_name="test", client_title="test", client_version="1"
        )

    async def test_forced_close_stops_what_a_launcher_shim_spawned(
        self, tmp_path: Path
    ) -> None:
        """A shim's child would otherwise keep running in the room's workdir."""
        client = CodexStdioClient(
            command=[sys.executable, "-c", SHIM],
            cwd=str(tmp_path),
        )
        await client.connect()
        heartbeat = tmp_path / HEARTBEAT_FILE
        await wait_for_heartbeat(heartbeat)

        # A surviving child also holds the pipes that close() waits to drain.
        await asyncio.wait_for(client.close(), timeout=CLOSE_BOUND_S)
        await assert_heartbeat_stopped(heartbeat)

    async def test_close_reaps_a_worker_when_its_launcher_exits_on_stdin_eof(
        self, tmp_path: Path
    ) -> None:
        """A launcher gets to exit on stdin EOF, but its child still holding
        the workdir is reaped by the tree kill that follows."""
        workdir = tmp_path / "room"
        workdir.mkdir()
        client = CodexStdioClient(
            command=[sys.executable, "-c", EXITING_SHIM],
            cwd=str(workdir),
        )
        await asyncio.wait_for(client.connect(), timeout=CLOSE_BOUND_S)
        try:
            heartbeat = workdir / HEARTBEAT_FILE
            await asyncio.wait_for(wait_for_heartbeat(heartbeat), timeout=CLOSE_BOUND_S)
            await asyncio.wait_for(client.close(), timeout=CLOSE_BOUND_S)
            await assert_heartbeat_stopped(heartbeat)
            assert (workdir / STDIN_EOF_MARKER).exists()
            shutil.rmtree(workdir)
        finally:
            await asyncio.wait_for(client.close(), timeout=CLOSE_BOUND_S)
        assert not workdir.exists()

    async def test_close_reaps_a_worker_when_its_launcher_has_already_exited(
        self, tmp_path: Path
    ) -> None:
        """A launcher that exits after starting the worker leaves that worker
        holding the workdir."""
        workdir = tmp_path / "room"
        workdir.mkdir()
        client = CodexStdioClient(
            command=[sys.executable, "-c", EXITED_LAUNCHER],
            cwd=str(workdir),
        )
        await client.connect()
        try:
            heartbeat = workdir / HEARTBEAT_FILE
            await asyncio.wait_for(wait_for_heartbeat(heartbeat), timeout=CLOSE_BOUND_S)
            process = client._proc
            assert process is not None
            await asyncio.wait_for(wait_until_exited(process), timeout=CLOSE_BOUND_S)
            still_running = heartbeat.stat().st_size
            await asyncio.sleep(QUIET_S)
            assert heartbeat.stat().st_size > still_running
            await asyncio.wait_for(client.close(), timeout=CLOSE_BOUND_S)
            await assert_heartbeat_stopped(heartbeat)
            shutil.rmtree(workdir)
        finally:
            await asyncio.wait_for(client.close(), timeout=CLOSE_BOUND_S)
        assert not workdir.exists()

    async def test_missing_status_fails_before_the_ack_timeout(self) -> None:
        """A helper that exits without a status line is end-of-file, not a 30s wait."""
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        try:
            started = time.monotonic()
            with pytest.raises(SpawnFailure):
                await _read_status(read_fd, CLOSE_BOUND_S)
            elapsed = time.monotonic() - started
        finally:
            with suppress(OSError):
                os.close(read_fd)
        assert elapsed < 1

    def test_job_open_failure_reports_its_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A job the helper cannot open is a status line, not a silent exit."""
        written: list[int | None] = []

        def deny(_name: str) -> object:
            error = OSError(5, "denied")
            error.winerror = 5  # type: ignore[attr-defined]
            raise error

        monkeypatch.setattr("band.integrations.codex.winspawn._open_job", deny)
        monkeypatch.setattr(
            "band.integrations.codex.winspawn._write_status",
            lambda _handle, code: written.append(code),
        )
        assert _start_and_wait("missing", 1, ("codex",)) == 1
        assert written == [5]

    @pytest.mark.parametrize(
        ("active", "drained"),
        [(0, True), (1, False), (None, False)],
    )
    async def test_idle_wait_preserves_ownership_until_explicit_release(
        self, active: int | None, drained: bool
    ) -> None:
        job = Job.__new__(Job)
        job._handle = object()  # type: ignore[assignment]
        job.name = "test"
        closed = False

        def active_processes() -> int | None:
            return active

        def close() -> None:
            nonlocal closed
            closed = True
            job._handle = None

        job.active_processes = active_processes  # type: ignore[method-assign]
        job.close = close  # type: ignore[method-assign]
        if drained:
            await job.wait_until_idle(0)
        else:
            with pytest.raises(JobNotDrained):
                await job.wait_until_idle(0)
        assert not closed

    def test_windows_helper_does_not_import_a_room_package(
        self, tmp_path: Path
    ) -> None:
        workdir = tmp_path / "room"
        workdir.mkdir()
        (workdir / "band.py").write_text("raise SystemExit('shadowed')\n")
        client = CodexStdioClient(
            command=[sys.executable, "-c", "pass"],
            cwd=str(workdir),
        )
        argv = client._windows_command("job", 1)
        completed = subprocess.run(
            argv[:4],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=CLOSE_BOUND_S,
            check=False,
        )
        assert "shadowed" not in completed.stderr
        assert "shadowed" not in completed.stdout
        assert completed.returncode == 2

    def test_windows_helper_is_not_already_imported(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-W", "error", str(HELPER_PATH)],
            capture_output=True,
            text=True,
            timeout=CLOSE_BOUND_S,
            check=False,
        )
        assert "RuntimeWarning" not in completed.stderr
        assert completed.returncode == 2

    def test_windows_command_does_not_resolve_a_binary_in_the_room(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PATH", str(Path(sys.executable).parent))
        workdir = tmp_path / "room"
        workdir.mkdir()
        (workdir / "definitely-missing-codex").write_text("")
        client = CodexStdioClient(
            command=["definitely-missing-codex", "app-server"],
            cwd=str(workdir),
        )
        with pytest.raises(FileNotFoundError):
            client._windows_command("job", 1)
        named = Path(sys.executable).name
        resolved = CodexStdioClient(command=[named, "-c", "pass"])._windows_command(
            "job", 1
        )
        assert Path(resolved[resolved.index("--") + 1]).is_absolute()

    @pytest.mark.skipif(os.name == "nt", reason="session-group teardown is Unix")
    @pytest.mark.parametrize(
        ("pid_alive", "signals_group"),
        [(False, True), (True, False)],
    )
    async def test_close_signals_a_session_only_while_its_leader_pid_is_free(
        self, monkeypatch: pytest.MonkeyPatch, pid_alive: bool, signals_group: bool
    ) -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "raise SystemExit(0)",
            start_new_session=True,
        )
        await process.wait()
        signaled: list[int] = []

        def record_killpg(pid: int, _sig: int) -> None:
            signaled.append(pid)

        def probe(pid: int, sig: int) -> None:
            if sig != 0:
                raise AssertionError(sig)
            if not pid_alive:
                raise ProcessLookupError(pid)

        monkeypatch.setattr(stdio_client.os, "killpg", record_killpg)
        monkeypatch.setattr(stdio_client.os, "kill", probe)
        await kill_process_tree(process)
        assert signaled == ([process.pid] if signals_group else [])
