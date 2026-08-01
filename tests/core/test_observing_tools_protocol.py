"""Minimal forwarding contract for the delivery-observing tools wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock

from band.core.turn.observing import ObservingTools
from band.core.protocols import AgentToolsProtocol


def test_unmodified_agent_tools_protocol_members_forward_to_inner() -> None:
    """Protocol growth remains visible through the observer proxy."""
    overridden = {
        "send_message",
        "execute_tool_call",
        "execute_tool_call_structured",
        "participants",
    }
    members = {
        name
        for name in AgentToolsProtocol.__dict__
        if not name.startswith("_") and name not in overridden
    }
    inner = MagicMock()
    for name in members:
        setattr(inner, name, MagicMock(name=f"inner.{name}"))
    inner.participants = []
    observing = ObservingTools(_inner=inner)

    for name in members:
        assert getattr(observing, name) is getattr(inner, name)
    assert observing.participants is inner.participants
