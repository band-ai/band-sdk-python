"""Agno history converter."""

from __future__ import annotations

import json
import logging
from typing import Any

from band.core.protocols import HistoryConverter

from .parsing import parse_tool_call, parse_tool_result

try:
    from agno.models.message import Message
except ImportError as e:
    raise ImportError(
        "agno is required for the Agno converter.\n"
        "Install with: pip install 'band-sdk[agno]'"
    ) from e

logger = logging.getLogger(__name__)

AgnoMessages = list[Message]


def _flush_tool_calls(
    messages: AgnoMessages, pending_calls: list[dict[str, Any]]
) -> None:
    if not pending_calls:
        return
    messages.append(
        Message(
            role="assistant",
            content=None,
            tool_calls=list(pending_calls),
            from_history=True,
        )
    )
    pending_calls.clear()


class AgnoHistoryConverter(HistoryConverter[AgnoMessages]):
    """Convert platform history to Agno messages."""

    def __init__(self, agent_name: str = "") -> None:
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> AgnoMessages:
        messages: AgnoMessages = []
        pending_calls: list[dict[str, Any]] = []

        for hist in raw:
            match hist.get("message_type", "text"):
                case "tool_call":
                    call = self._tool_call_dict(hist.get("content", ""))
                    if call is not None:
                        pending_calls.append(call)
                case "tool_result":
                    _flush_tool_calls(messages, pending_calls)
                    self._append_tool_result(messages, hist.get("content", ""))
                case "text":
                    _flush_tool_calls(messages, pending_calls)
                    messages.append(self._text_message(hist))
                case _:
                    pass

        _flush_tool_calls(messages, pending_calls)
        logger.debug(
            "Converted %d platform event(s) into %d Agno message(s)",
            len(raw),
            len(messages),
        )
        return messages

    @staticmethod
    def _tool_call_dict(content: str) -> dict[str, Any] | None:
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

    @staticmethod
    def _append_tool_result(messages: AgnoMessages, content: str) -> None:
        parsed = parse_tool_result(content)
        if parsed is None:
            return
        messages.append(
            Message(
                role="tool",
                tool_call_id=parsed.tool_call_id,
                tool_name=parsed.name,
                content=parsed.output,
                tool_call_error=parsed.is_error,
                from_history=True,
            )
        )

    def _text_message(self, hist: dict[str, Any]) -> Message:
        # Converter output is rehydrated history; tag it so Agno's
        # any(msg.from_history) check doesn't re-add stored session history.
        content = hist.get("content", "")
        # Own-agent detection keys on sender_name, not a stable sender_id:
        # formatted history dicts carry only sender_name (see
        # band.runtime.formatters.format_message_for_llm). If two participants
        # share a display name, or this agent is renamed, prior assistant turns
        # may be mis-mapped to the user role.
        if hist.get("role") == "assistant" and hist.get("sender_name") == (
            self._agent_name
        ):
            return Message(role="assistant", content=content, from_history=True)

        sender_name = hist.get("sender_name", "")
        formatted = f"[{sender_name}]: {content}" if sender_name else content
        return Message(role="user", content=formatted, from_history=True)
