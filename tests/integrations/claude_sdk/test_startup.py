"""Retained transport ownership closes failures before SDK Query construction."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions

from band.integrations.claude_sdk import transport
from band.integrations.claude_sdk.session_manager import ClaudeSessionManager
from band.workspaces import RoomWorkspaces
from tests.adapters.claude_sdk.process import WorkspaceProcess


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_pre_query_startup_failure_reaps_real_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup_fails: bool
) -> None:
    children: list[WorkspaceProcess] = []

    def launch(*, prompt: str, options: ClaudeAgentOptions) -> WorkspaceProcess:
        child = WorkspaceProcess(prompt=prompt, options=options)
        child.refuse_close = cleanup_fails
        children.append(child)
        return child

    monkeypatch.setattr(transport, "SubprocessCLITransport", launch)
    monkeypatch.setenv("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "invalid")
    manager = ClaudeSessionManager(
        ClaudeAgentOptions(cli_path=sys.executable, env={"PROBE_MARKER": "startup"}),
        workspaces=RoomWorkspaces(lambda _: str(tmp_path / "shared")),
    )
    try:
        expected_error = RuntimeError if cleanup_fails else ValueError
        with pytest.raises(expected_error):
            await manager.get_or_create_session("room-a")
        assert len(children) == 1
        if cleanup_fails:
            assert not children[0].exited
            assert manager.has_session("room-a")
            with pytest.raises(ValueError, match="both"):
                await manager.get_or_create_session("room-b")
            with pytest.raises(RuntimeError, match="cleanup failed"):
                await manager.get_or_create_session("room-a")
            assert len(children) == 1
            children[0].refuse_close = False
            await manager.cleanup_session("room-a")
        assert children[0].exited
        assert not manager.has_session("room-a")
    finally:
        for child in children:
            child.refuse_close = False
        await manager.stop()
