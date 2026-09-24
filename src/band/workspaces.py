"""Workspace selection for room-owned local coding agents."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

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
        resolved_workspace = os.path.realpath(workspace)
        Path(resolved_workspace).mkdir(parents=True, exist_ok=True)
        return resolved_workspace

    return create_room_workspace_resolver(Path.cwd() / _DEFAULT_WORKSPACE_DIRECTORY)(
        room_id
    )


def claim_room_workspace(
    room_id: str,
    workspace: str,
    workspace_rooms: dict[str, str],
) -> None:
    """Record one room as the live owner of a resolved workspace path."""
    owner = workspace_rooms.get(workspace)
    if owner is not None and owner != room_id:
        raise ValueError(
            f"workspace_for_room assigned {workspace!r} to both {owner!r} and {room_id!r}"
        )
    workspace_rooms[workspace] = room_id


def release_room_workspace(
    room_id: str,
    workspace: str,
    workspace_rooms: dict[str, str],
) -> None:
    """Release a room's claim on a workspace -- a no-op if it isn't the current owner.

    The ownership check matters whenever a release can race a later claim (a
    room's failed startup releasing after another room has since taken the
    same path): an unconditional pop would evict that new owner's claim
    instead of the stale one this caller actually meant to release.
    """
    owner = workspace_rooms.get(workspace)
    if owner != room_id:
        if owner is not None:
            logger.debug(
                "Skipped releasing workspace %r for room %r -- now owned by %r",
                workspace,
                room_id,
                owner,
            )
        return
    del workspace_rooms[workspace]


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
