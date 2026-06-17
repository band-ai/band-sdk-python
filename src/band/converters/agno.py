"""Agno history converter."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from importlib import import_module
from typing import TYPE_CHECKING, Any

from band.core.protocols import HistoryConverter

from ._tool_parsing import parse_tool_call, parse_tool_result

if TYPE_CHECKING:
    from agno.models.message import Message
    from agno.tools.function import Function

logger = logging.getLogger(__name__)

# Forward-referenced so this module imports without agno installed; the real
# Agno types are resolved lazily via the accessors below.
AgnoMessages = list["Message"]


def _require_agno(module: str, attr: str) -> Any:
    """Import an Agno attribute lazily with a clear error if agno is missing."""
    try:
        return getattr(import_module(module), attr)
    except ImportError as e:
        raise ImportError(
            "Agno dependencies not installed. Install with: uv add band-sdk[agno]"
        ) from e


@lru_cache(maxsize=1)
def agno_message_class() -> type[Message]:
    """Agno's ``Message`` class, imported lazily and once.

    Single home for this optional-dependency type; shared by the converter and
    the AgnoAdapter so the import lives in one place.
    """
    return _require_agno("agno.models.message", "Message")


@lru_cache(maxsize=1)
def agno_function_class() -> type[Function]:
    """Agno's ``Function`` class, imported lazily and once."""
    return _require_agno("agno.tools.function", "Function")


class AgnoHistoryConverter(HistoryConverter[AgnoMessages]):
    """
    Convert platform history to Agno message format.

    Output:
    - this agent's text messages -> Message(role="assistant", content=...)
    - everyone else's text messages -> Message(role="user", content="[name]: ...")
    - tool_call events -> Message(role="assistant", tool_calls=[{id, type, function}])
      (consecutive calls are batched into one assistant message)
    - tool_result events -> Message(role="tool", tool_call_id=..., content=output)

    This is Agno's own history shape, so rehydrated tool turns round-trip back
    through ``Agent.arun(input=...)`` for whichever model the agent uses.
    """

    def __init__(self, agent_name: str = ""):
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> AgnoMessages:
        """Dispatch each platform event to its Agno-message builder."""
        messages: AgnoMessages = []
        # Buffer consecutive tool calls so they land in a single assistant
        # message (matching how Agno emits parallel tool calls).
        pending_calls: list[dict[str, Any]] = []

        for hist in raw:
            match hist.get("message_type", "text"):
                case "tool_call":
                    call = self._tool_call_dict(hist.get("content", ""))
                    if call is not None:
                        pending_calls.append(call)
                case "tool_result":
                    # The assistant tool_calls message must precede its results.
                    self._flush_tool_calls(messages, pending_calls)
                    self._append_tool_result(messages, hist.get("content", ""))
                case "text":
                    self._flush_tool_calls(messages, pending_calls)
                    messages.append(self._text_message(hist))
                case _:
                    pass  # skip thought and other non-text, non-tool events

        self._flush_tool_calls(messages, pending_calls)
        logger.debug(
            "Converted %d platform event(s) into %d Agno message(s)",
            len(raw),
            len(messages),
        )
        return messages

    @staticmethod
    def _tool_call_dict(content: str) -> dict[str, Any] | None:
        """Shape a tool_call event into an Agno (OpenAI-style) tool call."""
        parsed = parse_tool_call(content)
        if parsed is None:
            return None
        return {
            "id": parsed.tool_call_id,
            "type": "function",
            "function": {
                "name": parsed.name,
                "arguments": json.dumps(parsed.args),
            },
        }

    def _flush_tool_calls(
        self, messages: AgnoMessages, pending_calls: list[dict[str, Any]]
    ) -> None:
        """Emit buffered tool calls as one assistant message, then clear them."""
        if not pending_calls:
            return
        message_cls = agno_message_class()
        messages.append(
            message_cls(role="assistant", content=None, tool_calls=list(pending_calls))
        )
        pending_calls.clear()

    def _append_tool_result(self, messages: AgnoMessages, content: str) -> None:
        """Append a tool_result event as a tool-role message."""
        parsed = parse_tool_result(content)
        if parsed is None:
            return
        message_cls = agno_message_class()
        messages.append(
            message_cls(
                role="tool",
                tool_call_id=parsed.tool_call_id,
                tool_name=parsed.name,
                content=parsed.output,
                tool_call_error=parsed.is_error,
            )
        )

    def _text_message(self, hist: dict[str, Any]) -> Message:
        """Map a text event to a user/assistant message with sender attribution."""
        message_cls = agno_message_class()
        content = hist.get("content", "")
        if hist.get("role") == "assistant" and hist.get("sender_name") == (
            self._agent_name
        ):
            return message_cls(role="assistant", content=content)

        sender_name = hist.get("sender_name", "")
        formatted = f"[{sender_name}]: {content}" if sender_name else content
        return message_cls(role="user", content=formatted)
