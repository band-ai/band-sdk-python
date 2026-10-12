"""Fixtures for the Room workspace lifecycle tests."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path

import pytest
import pytest_asyncio
from claude_agent_sdk import ClaudeAgentOptions

from band.adapters.claude_sdk import (
    ClaudeApprovalOptions,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
)
from band.adapters.codex import (
    CodexAdapter,
    CodexAdapterConfig,
)
from band.integrations.claude_sdk import transport
from tests.adapters.lifecycle.kit import (
    ClaudePeer,
    CodexPeer,
    RetentionHost,
)
from tests.paths import REPO_ROOT


@pytest_asyncio.fixture(params=["codex", "claude"], loop_scope="function")
async def host(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[RetentionHost]:
    failure = tmp_path / "fail-startup"
    hold = tmp_path / "hold-startup"
    arguments = ("--fail-startup", str(failure), "--hold-startup", str(hold))
    if request.param == "codex":
        adapter = CodexAdapter(
            CodexAdapterConfig(
                model="peer",
                workspace_for_room=lambda room: host.workspace(room),
                codex_command=(
                    sys.executable,
                    "-u",
                    str(REPO_ROOT / "tests/adapters/roompeer.py"),
                    *arguments,
                    "--home",
                    str(tmp_path / "codex-home"),
                    "--stay",
                ),
            )
        )

        def codex_child(
            *,
            command: Sequence[str] | None,
            cwd: str | None,
            env: Mapping[str, str] | None,
        ) -> CodexPeer:
            child = CodexPeer(command=command, cwd=cwd, env=env)
            child.starting, host.next_start = host.next_start, None
            host.spawned(child, cwd)
            return child

        monkeypatch.setattr("band.adapters.codex.CodexStdioClient", codex_child)
    else:
        adapter = ClaudeSDKAdapter(
            ClaudeSDKAdapterConfig(
                approvals=ClaudeApprovalOptions(
                    mode="auto_accept", text_notifications=False
                )
            ),
            workspace_for_room=lambda room: host.workspace(room),
        )

        def claude_child(*, prompt: str, options: ClaudeAgentOptions) -> ClaudePeer:
            child = ClaudePeer(prompt=prompt, options=options)
            child.arguments = arguments
            host.spawned(child, str(options.cwd) if options.cwd else None)
            return child

        monkeypatch.setattr(transport, "SubprocessCLITransport", claude_child)
    host = RetentionHost(adapter, tmp_path, failure, hold)
    await adapter.on_started("Peer", "A subprocess under test")
    try:
        yield host
    finally:
        failure.unlink(missing_ok=True)
        hold.unlink(missing_ok=True)
        for child in host.children:
            child.refuse_close = False
            for barrier in (getattr(child, "starting", None), child.closing):
                if barrier is not None:
                    barrier.released.set()
        try:
            await adapter.cleanup_all()
        finally:
            for child in host.children:
                await child.close()
