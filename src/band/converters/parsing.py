"""Shared tool event parsing utilities for history converters.

This module provides common parsing logic for tool_call and tool_result events
to reduce code duplication across converters.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from band.core.types import ToolEventKey

logger = logging.getLogger(__name__)


@dataclass
class ParsedToolCall:
    """Parsed tool_call event data."""

    name: str
    args: dict[str, Any]
    tool_call_id: str


@dataclass
class ParsedToolResult:
    """Parsed tool_result event data."""

    name: str
    output: str
    tool_call_id: str
    is_error: bool = False


def parse_tool_call(content: str) -> ParsedToolCall | None:
    """Parse a tool_call event from JSON content.

    Expected format: {"name": "...", "args": {...}, "tool_call_id": "..."}

    Args:
        content: JSON string containing tool call data

    Returns:
        ParsedToolCall if successful, None if parsing fails or required fields missing
    """
    try:
        event = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Failed to parse tool_call: %s", repr(content[:100]))
        return None

    if not isinstance(event, dict):
        logger.warning(
            "Skipping tool_call with non-object payload: %s", repr(content[:100])
        )
        return None

    tool_call_id = event.get(ToolEventKey.TOOL_CALL_ID)
    tool_name = event.get(ToolEventKey.NAME)

    if not isinstance(tool_call_id, str) or not tool_call_id:
        logger.warning(
            "Skipping tool_call with missing tool_call_id: %s",
            repr(content[:100]),
        )
        return None

    if not isinstance(tool_name, str) or not tool_name:
        logger.warning(
            "Skipping tool_call with missing name: %s",
            repr(content[:100]),
        )
        return None

    args = event.get(ToolEventKey.ARGS, {})
    if not isinstance(args, dict):
        logger.warning(
            "Tool_call args were not an object; using empty args: %s",
            repr(content[:100]),
        )
        args = {}

    return ParsedToolCall(
        name=tool_name,
        args=args,
        tool_call_id=tool_call_id,
    )


def parse_tool_result(content: str) -> ParsedToolResult | None:
    """Parse a tool_result event from JSON content.

    Expected format: {"name": "...", "output": "...", "tool_call_id": "...", "is_error": bool}

    Args:
        content: JSON string containing tool result data

    Returns:
        ParsedToolResult if successful, None if parsing fails or required fields missing
    """
    try:
        event = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("Failed to parse tool_result: %s", repr(content[:100]))
        return None

    if not isinstance(event, dict):
        logger.warning(
            "Skipping tool_result with non-object payload: %s", repr(content[:100])
        )
        return None

    tool_call_id = event.get(ToolEventKey.TOOL_CALL_ID)
    tool_name = event.get(ToolEventKey.NAME)

    if not isinstance(tool_call_id, str) or not tool_call_id:
        logger.warning(
            "Skipping tool_result with missing tool_call_id: %s",
            repr(content[:100]),
        )
        return None

    if not isinstance(tool_name, str) or not tool_name:
        logger.warning(
            "Skipping tool_result with missing name: %s",
            repr(content[:100]),
        )
        return None

    return ParsedToolResult(
        name=tool_name,
        output=str(event.get(ToolEventKey.OUTPUT, "")),
        tool_call_id=tool_call_id,
        is_error=bool(event.get(ToolEventKey.IS_ERROR, False)),
    )
