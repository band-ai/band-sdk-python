"""Point-in-time status snapshot of a running agent, for host dashboards."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from band.client.streaming import WebSocketDisconnectReason
from band.runtime.execution import ExecutionState


class RoomStatus(BaseModel):
    """One room the agent is in, and what its execution is doing.

    ``state`` is ``None`` for a custom ``Execution`` that does not expose the
    runtime's ``ExecutionState``.
    """

    model_config = ConfigDict(frozen=True)

    room_id: str
    state: ExecutionState | None


class AgentStatus(BaseModel):
    """An immutable copy of the agent's connection and room state.

    Built synchronously from in-memory state (no I/O, no locks), so it is
    safe to call from a heartbeat. Later runtime changes never alter a
    snapshot already taken.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    connected: bool
    last_disconnect_reason: WebSocketDisconnectReason | None
    started_at: datetime | None
    rooms: tuple[RoomStatus, ...]


__all__ = ["AgentStatus", "RoomStatus"]
