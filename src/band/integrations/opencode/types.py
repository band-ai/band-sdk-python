"""Typed session state for the OpenCode integration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OpencodeSessionState:
    """Each room's resumable OpenCode session, read from its resume records.

    ``session_registrations`` names the MCP registration that recorded each
    session.
    """

    room_to_session: dict[str, str] = field(default_factory=dict)
    session_registrations: dict[str, str] = field(default_factory=dict)
    replay_messages: list[str] = field(default_factory=list)
