"""Per-turn run plumbing: cancellation, event sink, context, stream."""

from __future__ import annotations

from typing import Any

__all__ = ["AgentStream"]


def __getattr__(name: str) -> Any:
    if name == "AgentStream":
        from band.core.run.stream import AgentStream

        return AgentStream
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
