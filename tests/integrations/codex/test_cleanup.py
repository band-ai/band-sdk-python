"""Subprocess cleanup survives cancelled callers, retries a failed kill, and
stops everything the launched process spawned."""

from __future__ import annotations

import asyncio
import sys
import textwrap
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from band.integrations.codex import CodexStdioClient, stdio_client
from tests.paths import REPO_ROOT

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


@pytest_asyncio.fixture(loop_scope="function")
async def child(
    tmp_path: Path,
) -> AsyncIterator[tuple[CodexStdioClient, asyncio.subprocess.Process]]:
    client = CodexStdioClient(
        command=[
            sys.executable,
            "-u",
            str(REPO_ROOT / "tests/adapters/roompeer.py"),
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


class TestSubprocessCleanup:
    @pytest.mark.parametrize("cancel_caller", [False, True], ids=["timeout", "cancel"])
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
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(client.close(), timeout=0.05)
        await asyncio.wait_for(process.wait(), timeout=3)
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

        with monkeypatch.context() as patch:
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

        stopped_at = heartbeat.stat().st_size
        await asyncio.sleep(QUIET_S)
        assert heartbeat.stat().st_size == stopped_at
