"""Workspace selection for room-owned local coding agents."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

WorkspaceResolver = Callable[[str], str]

_DEFAULT_WORKSPACE_DIRECTORY = ".band-workspaces"


def resolve_room_workspace(
    room_id: str, workspace_for_room: WorkspaceResolver | None
) -> str:
    """Return a room's absolute workspace, creating the safe default on demand."""
    if workspace_for_room is not None:
        workspace = workspace_for_room(room_id)
        if not isinstance(workspace, str) or not os.path.isabs(workspace):
            raise ValueError("workspace_for_room must return an absolute path")
        return os.path.realpath(workspace)

    return create_room_workspace_resolver(Path.cwd() / _DEFAULT_WORKSPACE_DIRECTORY)(room_id)


def create_room_workspace_resolver(root: str | Path) -> WorkspaceResolver:
    """Build a resolver that creates an isolated child workspace per room."""
    workspace_root = Path(root).expanduser().resolve()

    def workspace_for_room(room_id: str) -> str:
        workspace = (workspace_root / room_id).resolve()
        if workspace_root not in workspace.parents:
            raise ValueError("room id cannot escape the workspace root")
        workspace.mkdir(parents=True, exist_ok=True)
        return str(workspace)

    return workspace_for_room
