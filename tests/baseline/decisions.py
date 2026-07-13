"""Framework-neutral model decisions used by baseline scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    """A model-directed call to a platform tool."""

    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelDecision:
    """One deterministic model response: text, tool calls, or both."""

    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    @classmethod
    def text_reply(cls, text: str) -> ModelDecision:
        """Build a terminal text response."""
        return cls(text=text)

    @classmethod
    def call(cls, name: str, **arguments: Any) -> ModelDecision:
        """Build a response containing one platform-tool call."""
        return cls(tool_calls=(ToolCall(name=name, arguments=arguments),))
