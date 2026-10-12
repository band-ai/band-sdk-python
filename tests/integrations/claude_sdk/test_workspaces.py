"""Real SDK control messages and child processes across workspace restarts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage
from claude_agent_sdk.types import (
    CanUseTool,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from band.integrations.claude_sdk import transport
from band.integrations.claude_sdk.session_manager import (
    ClaudeRoomSession,
    ClaudeSessionManager,
)
from band.workspaces import RoomWorkspaces, create_room_workspace_resolver
from tests.adapters.claude_sdk.helpers import exchange
from tests.adapters.claude_sdk.process import WorkspacePeer


async def file_operation(
    session: ClaudeRoomSession, tool_name: str, **arguments: str
) -> tuple[bool, str | None]:
    request = json.dumps({"tool_name": tool_name, "input": arguments})
    match await exchange(session, request):
        case [*_, ResultMessage() as result]:
            return result.is_error, result.result
    raise AssertionError("The child returned no result")


async def test_real_sdk_permissions_and_files_survive_room_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    children: list[WorkspacePeer] = []
    requests: list[tuple[str, str]] = []

    def launch(*, prompt: str, options: ClaudeAgentOptions) -> WorkspacePeer:
        child = WorkspacePeer(prompt=prompt, options=options)
        children.append(child)
        return child

    def permissions(session: ClaudeRoomSession, generation: int) -> CanUseTool:
        async def decide(
            tool_name: str, arguments: dict[str, Any], context: ToolPermissionContext
        ) -> PermissionResult:
            requests.append((session.room_id, tool_name))
            if arguments.get("content") == "denied":
                return PermissionResultDeny(message="Refused by the host")
            updated = {**arguments}
            if tool_name == "Write":
                updated["content"] = arguments["content"].upper()
            return PermissionResultAllow(updated_input=updated)

        return decide

    monkeypatch.setattr(transport, "SubprocessCLITransport", launch)
    workspaces = RoomWorkspaces(create_room_workspace_resolver(tmp_path))

    def new_manager() -> ClaudeSessionManager:
        return ClaudeSessionManager(
            ClaudeAgentOptions(cli_path=sys.executable),
            workspaces=workspaces,
            can_use_tool_factory=permissions,
        )

    manager = new_manager()
    try:
        for room_id in ("room-a", "room-b"):
            assert await file_operation(
                manager.room(room_id), "Write", file_path="notes.txt", content=room_id
            ) == (False, room_id.upper())
        assert await file_operation(
            manager.room("room-a"), "Write", file_path="notes.txt", content="denied"
        ) == (True, "denied")
        assert len(children) == 2
        assert all(not child.exited for child in children)
        assert (tmp_path / "room-a" / "notes.txt").read_text() == "ROOM-A"
        assert (tmp_path / "room-b" / "notes.txt").read_text() == "ROOM-B"
        assert not (tmp_path / "notes.txt").exists()
        await manager.stop()
        assert all(child.exited for child in children)

        manager = new_manager()
        for room_id in ("room-a", "room-b"):
            assert await file_operation(
                manager.room(room_id), "Read", file_path="notes.txt"
            ) == (
                False,
                room_id.upper(),
            )
        assert requests == [
            ("room-a", "Write"),
            ("room-b", "Write"),
            ("room-a", "Write"),
            ("room-a", "Read"),
            ("room-b", "Read"),
        ]
        assert len(children) == 4
    finally:
        await manager.stop()
    assert all(child.exited for child in children)
