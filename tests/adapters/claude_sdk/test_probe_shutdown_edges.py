"""Probe shutdown cannot orphan startup work or leave room admission open."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from anyio.abc import Process

from band.adapters.claude_sdk import (
    ClaudeCLIOptions,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
)
from band.core.exceptions import CleanupIncompleteError, RoomClosingError
from band.integrations.claude_sdk.session_manager import (
    ClaudeSessionManagerStoppedError,
)
from tests.adapters.claude_sdk.helpers import OpenRoom


@pytest.mark.skipif(
    os.name == "nt", reason="The real version peer uses a POSIX shebang"
)
async def test_cancelled_version_check_remains_owned_until_its_child_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "version-peer"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import signal\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('9.9.9 (Claude Code)', flush=True)\n"
        "while True: signal.pause()\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    children: list[Process] = []
    open_process = anyio.open_process

    async def observe_process(*args: Any, **kwargs: Any) -> Process:
        process = await open_process(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(anyio, "open_process", observe_process)
    adapter = ClaudeSDKAdapter(
        ClaudeSDKAdapterConfig(cli=ClaudeCLIOptions(cli_path=str(executable)))
    )
    joining: asyncio.Task[None] | None = None
    try:
        with pytest.raises(CleanupIncompleteError):
            await adapter.preflight(timeout_s=0.5)
        [child] = children
        assert child.returncode is None
        joining = asyncio.create_task(adapter.cleanup_all())
        await asyncio.sleep(0)
        assert not joining.done()
        child.kill()
        await child.wait()
        await joining
        assert child.returncode is not None
        assert adapter._session_manager is None
        assert adapter._mcp.current is None
    finally:
        for child in children:
            if child.returncode is None:
                child.kill()
            await child.wait()
        if joining is not None:
            await joining
        await adapter.cleanup_all()


async def test_room_admission_closes_before_waiting_for_probe_cleanup(
    claude_room: OpenRoom,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    room = await claude_room()
    room.claude.script([room.model_reply("ready")])
    await room.send("start")
    entered = asyncio.Event()
    released = asyncio.Event()

    async def close_probe() -> None:
        entered.set()
        await released.wait()

    owner = room.adapter._harness_probe

    async def discover() -> None:
        owner.own(close_probe)

    probing = asyncio.create_task(owner.run(discover, timeout_s=5))
    await entered.wait()
    joining = asyncio.Event()
    close = owner.close

    async def observe_join(*, timeout_s: float = 60) -> None:
        joining.set()
        await close(timeout_s=timeout_s)

    monkeypatch.setattr(owner, "close", observe_join)
    closing = asyncio.create_task(room.adapter.cleanup_all())
    try:
        await joining.wait()
        manager = room.adapter._session_manager
        assert manager is not None
        with pytest.raises(ClaudeSessionManagerStoppedError):
            manager.room("another-room")
        with pytest.raises(RoomClosingError):
            await room.send("arrived during shutdown")
        assert room.chat == ["ready"]
    finally:
        released.set()
        await closing
        await probing
