"""Workspace selection for room-owned local coding agents."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

WorkspaceResolver = Callable[[str], str]

_DEFAULT_WORKSPACE_DIRECTORY = ".band-workspaces"


class RoomWorkspaces:
    """Adapter-local workspace claims, stable until a room leaves."""

    def __init__(self, resolver: WorkspaceResolver | None = None) -> None:
        self._resolver = resolver
        self._paths: dict[str, str] = {}
        self._owners: dict[str, str] = {}

    @property
    def rooms(self) -> tuple[str, ...]:
        return tuple(self._paths)

    def workspace(self, room_id: str) -> str:
        return self._paths[room_id]

    def claim(self, room_id: str, workspace: str | None = None) -> str:
        path = self._paths.get(room_id)
        if path is None:
            path = workspace or resolve_room_workspace(room_id, self._resolver)
        claim_room_workspace(room_id, path, self._owners)
        self._paths[room_id] = path
        return path

    def release(self, room_id: str, workspace: str | None = None) -> None:
        path = self._paths.get(room_id)
        if path is None or (workspace is not None and workspace != path):
            return
        release_room_workspace(room_id, path, self._owners)
        del self._paths[room_id]


def is_host_absolute(path: str) -> bool:
    """Whether ``path`` is absolute on the running OS -- on Windows that also
    needs a drive or UNC share, so ``/opt/x`` is relative there."""
    return Path(path).is_absolute()


def resolve_room_workspace(
    room_id: str, workspace_for_room: WorkspaceResolver | None
) -> str:
    """Return a room's absolute workspace, creating the safe default on demand."""
    if workspace_for_room is not None:
        workspace = workspace_for_room(room_id)
        if not isinstance(workspace, str) or not is_host_absolute(workspace):
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


def workspace_resolver_for(
    cwd: str | None, workspace_for_room: WorkspaceResolver | None
) -> WorkspaceResolver | None:
    """Resolve an adapter's ``cwd``/``workspace_for_room`` config into one resolver.

    The two are mutually exclusive: ``cwd`` is a compatibility alias that
    becomes a per-room resolver rooted at it, while an explicit
    ``workspace_for_room`` is returned unchanged.
    """
    if cwd is None:
        return workspace_for_room
    if workspace_for_room is not None:
        raise ValueError("set either cwd or workspace_for_room, not both")
    return create_room_workspace_resolver(cwd)


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
