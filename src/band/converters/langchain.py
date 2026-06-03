"""LangChain/LangGraph history converter."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

try:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
except ImportError as e:
    raise ImportError(
        "LangChain dependencies not installed. Install with: uv add band-sdk[langgraph]"
    ) from e

from band.converters._tool_parsing import parse_tool_call, parse_tool_result
from band.core.protocols import HistoryConverter

logger = logging.getLogger(__name__)

# Type alias for LangChain messages
LangChainMessages = list[AIMessage | HumanMessage | ToolMessage]


@dataclass
class _PendingToolCall:
    name: str
    args: dict[str, Any]
    match_ids: tuple[str, ...]
    emit_tool_call_id: str


@dataclass
class _ParsedToolResult:
    name: str
    output: Any
    match_ids: tuple[str, ...]
    emit_tool_call_id: str


class LangChainHistoryConverter(HistoryConverter[LangChainMessages]):
    """
    Converts platform history to LangChain message types.

    Handles:
    - tool_call + tool_result pairing with tool_call_id extraction
    - Preserving this agent's visible text replies for restart hydration
    - Skipping own text only while a tool call is open, preserving LangChain ordering
    - Including other agents' messages as HumanMessage
    - User messages as HumanMessage
    """

    def __init__(self, agent_name: str = ""):
        """
        Initialize converter.

        Args:
            agent_name: Name of this agent. Visible text messages from this agent
                       are included as AIMessage for restart hydration, except
                       while a tool call is open because LangChain requires the
                       tool-call AIMessage to be followed by its ToolMessage.
                       Messages from other agents are included as HumanMessage.
        """
        self._agent_name = agent_name

    def set_agent_name(self, name: str) -> None:
        """
        Set agent name so converter knows which messages to skip.

        Args:
            name: Name of this agent
        """
        self._agent_name = name

    def convert(self, raw: list[dict[str, Any]]) -> LangChainMessages:
        """Convert platform history to LangChain messages."""
        messages: LangChainMessages = []
        pending_tool_calls: list[_PendingToolCall | dict[str, Any]] = []

        for hist in raw:
            message_type = hist.get("message_type", "text")
            content = hist.get("content", "")
            role = hist.get("role")
            sender_name = hist.get("sender_name", "")

            if message_type == "tool_call":
                parsed_call = parse_tool_call(content)
                if parsed_call:
                    pending_tool_calls.append(
                        _PendingToolCall(
                            name=parsed_call.name,
                            args=parsed_call.args,
                            match_ids=(parsed_call.tool_call_id,),
                            emit_tool_call_id=parsed_call.tool_call_id,
                        )
                    )
                    continue

                legacy_call = self._parse_legacy_tool_call(content)
                if legacy_call:
                    pending_tool_calls.append(legacy_call)

            elif message_type == "tool_result":
                parsed_result = parse_tool_result(content)
                if parsed_result:
                    tool_result = _ParsedToolResult(
                        name=parsed_result.name,
                        output=parsed_result.output,
                        match_ids=(parsed_result.tool_call_id,),
                        emit_tool_call_id=parsed_result.tool_call_id,
                    )
                else:
                    tool_result = self._parse_legacy_tool_result(content)
                    if not tool_result:
                        continue

                matching_call = self._pop_matching_tool_call(
                    pending_tool_calls,
                    tool_name=tool_result.name,
                    match_ids=tool_result.match_ids,
                )

                if not matching_call:
                    logger.warning(
                        "Skipping tool_result without matching tool_call: %s",
                        tool_result.name,
                    )
                    continue

                tool_call_id = tool_result.emit_tool_call_id or self._call_emit_id(
                    matching_call
                )
                messages.append(
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": tool_call_id,
                                "name": tool_result.name,
                                "args": self._call_args(matching_call),
                            }
                        ],
                    )
                )
                messages.append(
                    ToolMessage(
                        content=str(tool_result.output),
                        tool_call_id=tool_call_id,
                    )
                )

            elif message_type == "text":
                if role == "assistant" and sender_name == self._agent_name:
                    if pending_tool_calls:
                        # Text while a tool call is open would interrupt the
                        # required AIMessage(tool_calls) -> ToolMessage pairing.
                        logger.debug(
                            "Skipping own message in tool segment: %s", content[:50]
                        )
                    else:
                        messages.append(AIMessage(content=content))
                else:
                    # Include user messages AND other agents' messages
                    messages.append(HumanMessage(content=f"[{sender_name}]: {content}"))

        # Warn about unmatched tool calls
        if pending_tool_calls:
            logger.warning(
                "Found %s tool_calls without matching tool_results",
                len(pending_tool_calls),
            )

        return messages

    @staticmethod
    def _pop_matching_tool_call(
        pending_tool_calls: list[_PendingToolCall | dict[str, Any]],
        *,
        tool_name: str,
        match_ids: tuple[str, ...],
    ) -> _PendingToolCall | dict[str, Any] | None:
        for match_id in match_ids:
            for i, call in enumerate(pending_tool_calls):
                call_match_ids = LangChainHistoryConverter._call_match_ids(call)
                if match_id in call_match_ids:
                    return pending_tool_calls.pop(i)

        named_matches = [
            (i, call)
            for i, call in enumerate(pending_tool_calls)
            if LangChainHistoryConverter._call_name(call) == tool_name
        ]
        if not named_matches:
            return None

        if not match_ids:
            i, _call = named_matches[-1]
            logger.warning(
                "Falling back to tool_result name match without IDs: %s",
                tool_name,
            )
            return pending_tool_calls.pop(i)

        if len(named_matches) == 1:
            i, _call = named_matches[0]
            logger.warning(
                "Falling back to single pending tool_call by name after ID mismatch: %s",
                tool_name,
            )
            return pending_tool_calls.pop(i)

        logger.warning(
            "Skipping ambiguous tool_result with nonmatching IDs for %s",
            tool_name,
        )
        return None

    @staticmethod
    def _call_match_ids(call: _PendingToolCall | dict[str, Any]) -> tuple[str, ...]:
        if isinstance(call, _PendingToolCall):
            return call.match_ids

        ids: list[str] = []
        for key in ("match_ids", "tool_call_id", "run_id"):
            value = call.get(key)
            if isinstance(value, str):
                ids.append(value)
            elif isinstance(value, (tuple, list)):
                ids.extend(item for item in value if isinstance(item, str))
        return tuple(dict.fromkeys(ids))

    @staticmethod
    def _call_name(call: _PendingToolCall | dict[str, Any]) -> str | None:
        if isinstance(call, _PendingToolCall):
            return call.name
        value = call.get("name")
        return value if isinstance(value, str) else None

    @staticmethod
    def _call_args(call: _PendingToolCall | dict[str, Any]) -> dict[str, Any]:
        if isinstance(call, _PendingToolCall):
            return call.args
        value = call.get("args", {})
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _call_emit_id(call: _PendingToolCall | dict[str, Any]) -> str:
        if isinstance(call, _PendingToolCall):
            return call.emit_tool_call_id
        for key in ("emit_tool_call_id", "tool_call_id", "run_id"):
            value = call.get(key)
            if isinstance(value, str):
                return value
        return "unknown"

    @classmethod
    def _parse_legacy_tool_call(cls, content: str) -> _PendingToolCall | None:
        try:
            event = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Failed to parse tool_call: %s", content[:100])
            return None

        if not isinstance(event, dict):
            logger.warning("Skipping non-object tool_call: %s", content[:100])
            return None

        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        tool_name = event.get("name")
        if not isinstance(tool_name, str):
            logger.warning("Skipping tool_call with missing name: %s", content[:100])
            return None

        tool_input = data.get("input", {})
        if not isinstance(tool_input, dict):
            tool_input = {}

        run_id = event.get("run_id")
        match_ids = (run_id,) if isinstance(run_id, str) else ()
        return _PendingToolCall(
            name=tool_name,
            args=tool_input,
            match_ids=match_ids,
            emit_tool_call_id=run_id if isinstance(run_id, str) else "unknown",
        )

    @classmethod
    def _parse_legacy_tool_result(cls, content: str) -> _ParsedToolResult | None:
        try:
            event = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Failed to parse tool_result: %s", content[:100])
            return None

        if not isinstance(event, dict):
            logger.warning("Skipping non-object tool_result: %s", content[:100])
            return None

        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        tool_name = event.get("name")
        if not isinstance(tool_name, str):
            logger.warning("Skipping tool_result with missing name: %s", content[:100])
            return None

        output = data.get("output", "")
        run_id = event.get("run_id")
        extracted_tool_call_id = cls._extract_tool_call_id(output)

        match_ids = tuple(
            dict.fromkeys(
                value
                for value in (run_id, extracted_tool_call_id)
                if isinstance(value, str)
            )
        )
        emit_tool_call_id = extracted_tool_call_id or run_id
        if not isinstance(emit_tool_call_id, str):
            logger.warning(
                "Skipping tool_result with missing tool_call_id: %s", content[:100]
            )
            return None

        return _ParsedToolResult(
            name=tool_name,
            output=output,
            match_ids=match_ids,
            emit_tool_call_id=emit_tool_call_id,
        )

    @classmethod
    def _extract_tool_call_id(cls, output: Any) -> str | None:
        """Extract tool_call_id from current structured outputs or older string reprs."""
        if isinstance(output, dict):
            value = output.get("tool_call_id")
            if isinstance(value, str):
                return value

            messages = output.get("messages")
            if isinstance(messages, list):
                for message in messages:
                    extracted = cls._extract_tool_call_id(message)
                    if extracted:
                        return extracted

            nested_output = output.get("output")
            if nested_output is not None:
                return cls._extract_tool_call_id(nested_output)

            return None

        if isinstance(output, list):
            for item in output:
                extracted = cls._extract_tool_call_id(item)
                if extracted:
                    return extracted
            return None

        value = getattr(output, "tool_call_id", None)
        if isinstance(value, str):
            return value

        match = re.search(r"tool_call_id='([^']+)'", str(output))
        return match.group(1) if match else None
