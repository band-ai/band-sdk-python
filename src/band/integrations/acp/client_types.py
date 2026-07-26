"""Types and compatibility aliases for outbound ACP client integration."""

from __future__ import annotations

from dataclasses import dataclass, field

from band.integrations.acp.client_profiles import ACPClientProfile
from band.integrations.acp.client_runtime import ACPCollectingClient


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


class BandACPClient(ACPCollectingClient):
    """Compatibility wrapper around ``ACPCollectingClient``.

    Existing tests and e2e helpers still construct ``BandACPClient``
    directly. Keep this alias stable while bridge adapters choose the
    runtime-specific profile explicitly.
    """

    def __init__(self, profile: ACPClientProfile | None = None) -> None:
        super().__init__(profile=profile)


BandACPClient = BandACPClient
