"""Types and compatibility aliases for outbound ACP client integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from band.core.protocols import AgentToolsProtocol
from band.core.retention import ReleaseCause, Residency, RetainedRoom
from band.integrations.acp.client_runtime import ACPRuntime
from band.integrations.acp.collecting import ACPCollectingClient


@dataclass
class ACPClientSessionState:
    """Persisted room-to-session resume candidates for the ACP client adapter.

    A session ID is owned by the remote ACP agent, so the adapter must validate it
    with ACP ``session/load`` after reconnecting before it can be used for a prompt.

    ``replay_messages`` carries the room's text transcript as ``[sender]: content``
    lines, the fallback context when no persisted session can be restored.
    """

    room_to_session: dict[str, str] = field(default_factory=dict)
    replay_messages: list[str] = field(default_factory=list)


@dataclass
class RoomSession:
    """A room's ACP session and the Band MCP URL it dials, ``None`` when it
    dials none.

    ``live`` is whether the session is loaded on the room's current process;
    the identity outlives that process until the room is disposed.
    """

    session_id: str
    band_url: str | None
    bootstrap_pending: bool = True
    replay_pending: bool = True
    live: bool = True


@dataclass(eq=False)
class ACPRoom:
    """One room's ACP residency, composed with the ``RetainedRoom`` that owns
    its transitions.

    ``runtime`` is the room's process and connection, held until their closure
    is confirmed. ``session`` is the native identity, held across a physical
    release until final disposal. ``reservation`` serializes the room's prompts
    from session setup through their actual end. ``session_closes`` are
    best-effort closes of retired sessions, which finish before the process
    is released.
    """

    room_id: str
    workspace: str
    retained: RetainedRoom = field(init=False)
    runtime: ACPRuntime | None = None
    session: RoomSession | None = None
    tools: AgentToolsProtocol | None = None
    reservation: asyncio.Lock = field(default_factory=asyncio.Lock)
    session_closes: set[asyncio.Task[None]] = field(default_factory=set)

    @property
    def closing(self) -> bool:
        """Whether the room's final disposal has begun or finished."""
        snapshot = self.retained.snapshot
        return (
            snapshot.state == Residency.CLOSED
            or snapshot.pending_release == ReleaseCause.FINAL
        )

    @property
    def generation(self) -> int:
        """The residency the room's current process belongs to."""
        return self.retained.snapshot.generation

    @property
    def live_session(self) -> tuple[str, ACPRuntime] | None:
        """The session loaded on the room's current process, if any."""
        session, runtime = self.session, self.runtime
        if session is None or not session.live or runtime is None:
            return None
        return session.session_id, runtime


class BandACPClient(ACPCollectingClient):
    """Compatibility alias for ``ACPCollectingClient``.

    Existing tests and e2e helpers still construct ``BandACPClient``
    directly. Keep this alias stable while bridge adapters choose the
    runtime-specific profile explicitly.
    """
