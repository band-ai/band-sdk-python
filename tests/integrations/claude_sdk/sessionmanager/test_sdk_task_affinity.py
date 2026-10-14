"""The installed SDK runs one room session across tasks."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
)

from band.integrations.claude_sdk.session_manager import (
    ClaudeSessionManager,
)
from tests.adapters.claude_sdk.process import launch_children
from tests.integrations.claude_sdk.sessionmanager.kit import (
    TaskRecordingPeer,
    allow_everything,
)


class TestSdkTaskAffinity:
    async def test_the_installed_sdk_runs_one_session_across_tasks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Connect, query and close each run in a different task on one loop,
        against a real child process: the SDK floor needs no task-affine actor."""
        children = launch_children(monkeypatch, TaskRecordingPeer)
        manager = ClaudeSessionManager(
            ClaudeAgentOptions(cli_path=sys.executable, cwd=tmp_path),
            can_use_tool_factory=allow_everything,
        )
        session = manager.room("room-a")
        request = json.dumps(
            {"tool_name": "Write", "input": {"file_path": "notes.txt", "content": "x"}}
        )
        try:
            async with session.using() as client:
                with session.listening() as stream:
                    await asyncio.create_task(client.query(request))
                    [*_, result] = [message async for message in stream.until_result()]
            await asyncio.create_task(manager.cleanup_session("room-a"))
        finally:
            await manager.stop()

        [child] = children
        assert isinstance(result, ResultMessage)
        assert (result.is_error, result.result) == (False, "x")
        assert child.exited
        assert len({*child.tasks.values(), asyncio.current_task()}) == 4
