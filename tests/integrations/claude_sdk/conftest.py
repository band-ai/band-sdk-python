"""Claude session managers whose CLI children are real processes."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import pytest_asyncio
from claude_agent_sdk import ClaudeAgentOptions

from band.integrations.claude_sdk import transport
from band.integrations.claude_sdk.session_manager import ClaudeSessionManager
from band.workspaces import RoomWorkspaces
from tests.adapters.claude_sdk.process import WorkspaceProcess


@dataclass
class Launches:
    """The real CLI children a manager started, each failing its startup."""

    manager: ClaudeSessionManager
    children: list[WorkspaceProcess] = field(default_factory=list)
    # Whether each child launched from now on refuses every close.
    refuse_close: bool = False


@pytest_asyncio.fixture(loop_scope="function")
async def launches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Launches]:
    manager = ClaudeSessionManager(
        ClaudeAgentOptions(cli_path=sys.executable, env={"PROBE_MARKER": "startup"}),
        workspaces=RoomWorkspaces(lambda _: str(tmp_path / "shared")),
    )
    launched = Launches(manager=manager)

    def launch(*, prompt: str, options: ClaudeAgentOptions) -> WorkspaceProcess:
        child = WorkspaceProcess(prompt=prompt, options=options)
        child.refuse_close = launched.refuse_close
        launched.children.append(child)
        return child

    monkeypatch.setattr(transport, "SubprocessCLITransport", launch)
    monkeypatch.setenv("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "invalid")
    try:
        yield launched
    finally:
        for child in launched.children:
            child.refuse_close = False
        await manager.stop()
