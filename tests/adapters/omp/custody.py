"""Observe adapter room and workspace custody."""

from __future__ import annotations

from band.adapters.omp import (
    OmpAdapter,
)
from band.adapters.omp.roomstate import OmpRoom
from band.core.model_catalog import ModelSelection


def room_runtime(adapter: OmpAdapter, room_id: str) -> object | None:
    room = adapter._rooms.get(room_id)
    return None if room is None else room.runtime


def occupied_room_ids(adapter: OmpAdapter) -> set[str]:
    return set(adapter._rooms)


def claimed_workspace_rooms(adapter: OmpAdapter) -> tuple[str, ...]:
    return adapter._workspaces.rooms


def retained_selection(adapter: OmpAdapter, room_id: str) -> ModelSelection | None:
    return adapter._room_selections.get(room_id)


def owned_room(adapter: OmpAdapter, room_id: str) -> OmpRoom:
    """Expose the room whose runtime, workspace and startup files remain owned."""
    return adapter._rooms[room_id]
