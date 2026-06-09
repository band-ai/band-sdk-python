"""Types and compatibility aliases for outbound ACP client integration."""

from __future__ import annotations

from dataclasses import dataclass, field

from band.integrations.acp.client_profiles import ACPClientProfile
from band.integrations.acp.client_runtime import ACPCollectingClient


@dataclass
class ACPClientSessionState:
    """Session state for ACP client adapter rehydration."""

    room_to_session: dict[str, str] = field(default_factory=dict)


class BandACPClient(ACPCollectingClient):
    """Compatibility wrapper around ``ACPCollectingClient``.

    Existing tests and e2e helpers still construct ``BandACPClient``
    directly. Keep this alias stable while bridge adapters choose the
    runtime-specific profile explicitly.
    """

    def __init__(self, profile: ACPClientProfile | None = None) -> None:
        super().__init__(profile=profile)


BandACPClient = BandACPClient
