"""Cancelled callers cannot abandon or prevent retry of subprocess cleanup."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from band.integrations.codex import CodexStdioClient, stdio_client
from tests.paths import REPO_ROOT


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


@pytest.mark.parametrize("cancel_caller", [False, True], ids=["timeout", "cancel"])
async def test_interrupted_close_still_reaps_the_child(
    child: tuple[CodexStdioClient, asyncio.subprocess.Process], cancel_caller: bool
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
    child: tuple[CodexStdioClient, asyncio.subprocess.Process],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, process = child

    async def refuse_kill(_process: asyncio.subprocess.Process) -> None:
        raise RuntimeError("kill failed")

    with monkeypatch.context() as patch:
        patch.setattr(stdio_client, "_kill_process_tree", refuse_kill)
        with pytest.raises(RuntimeError, match="kill failed"):
            await client.close()
        assert process.returncode is None
    await client.close()
    assert process.returncode is not None


async def test_overlapping_close_waits_for_the_same_child_to_exit(
    child: tuple[CodexStdioClient, asyncio.subprocess.Process],
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
    child: tuple[CodexStdioClient, asyncio.subprocess.Process],
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
    await client.initialize(client_name="test", client_title="test", client_version="1")
