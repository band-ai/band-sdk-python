"""Collect per-turn messages, permissions and tool routing state."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import JsonValue

from band.core.protocols import AgentToolsProtocol


@dataclass
class OmpTurn:
    tools: AgentToolsProtocol
    calls: dict[str, tuple[str, dict[str, JsonValue]]] = field(default_factory=dict)
    closing_text: list[str] = field(default_factory=list)
    closed: bool = False
