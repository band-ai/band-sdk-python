"""Collect per-turn messages, permissions and tool routing state."""

from __future__ import annotations

from dataclasses import dataclass, field

from band.core.protocols import AgentToolsProtocol


@dataclass
class OmpTurn:
    tools: AgentToolsProtocol
    calls: dict[str, str] = field(default_factory=dict)
    closing_text: list[str] = field(default_factory=list)
    closed: bool = False
