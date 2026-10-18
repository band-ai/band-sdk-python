"""Immutable observations of the current agent and room owners."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from band.client.streaming.client import WebSocketDisconnectReason
from band.runtime.execution import ExecutionState
from band.runtime.rooms import RoomState


class RoomStatus(BaseModel):
    """Membership and execution custody for one locally tracked room.

    ``member`` is the presence roster's last reconciled membership, not a
    claim that its socket is currently connected. ``lifecycle`` describes
    the execution owner; ``state`` is the canonical execution state, or
    ``None`` for an absent/custom execution that does not expose it.
    """

    model_config = ConfigDict(frozen=True)

    room_id: str
    member: bool
    lifecycle: RoomState | None
    state: ExecutionState | None
    routed: bool
    unsettled: bool


class AgentStatus(BaseModel):
    """A synchronous, I/O-free copy, never a lifecycle authority.

    ``running`` means startup succeeded and no stop, terminal disconnect, or
    completed transport shutdown has ended the run. Cancelling a waiter leaves
    a live transport running; a transient outage only clears ``connected``.
    ``cleanup_pending`` includes metadata initialization and held identity
    guards before any execution runtime exists, as well as startup still in
    progress or cleanup that must be retried.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    running: bool
    connected: bool
    cleanup_pending: bool
    last_disconnect_reason: WebSocketDisconnectReason | None
    started_at: datetime | None
    rooms: tuple[RoomStatus, ...]


__all__ = ["AgentStatus", "RoomStatus"]
