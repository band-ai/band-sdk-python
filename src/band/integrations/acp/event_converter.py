"""Stateless converter: PlatformMessage -> ACP session_update chunk."""

from __future__ import annotations

import logging
from typing import Any

from acp import (
    plan_entry,
    start_tool_call,
    text_block,
    tool_content,
    update_agent_message_text,
    update_agent_thought_text,
    update_plan,
    update_tool_call,
)

from band.converters._tool_parsing import parse_tool_call, parse_tool_result
from band.core.types import PlatformMessage

logger = logging.getLogger(__name__)


class EventConverter:
    """Convert PlatformMessage to ACP session_update chunks.

    Stateless converter that maps each Band message type to the
    appropriate ACP session_update discriminator for rich streaming.
    """

    @staticmethod
    def convert(msg: PlatformMessage) -> Any | None:
        """Convert a PlatformMessage to an ACP session_update chunk.

        Args:
            msg: The platform message to convert.

        Returns:
            An ACP session_update chunk, or None if the message type
            is not mappable.
        """
        match msg.message_type:
            case "text":
                return update_agent_message_text(msg.content)
            case "thought":
                return update_agent_thought_text(msg.content)
            case "tool_call":
                return EventConverter._convert_tool_call(msg)
            case "tool_result":
                return EventConverter._convert_tool_result(msg)
            case "error":
                return update_agent_message_text(f"[Error] {msg.content}")
            case "task":
                return update_plan([plan_entry(msg.content, status="in_progress")])
            case _:
                logger.debug("Unmapped message type %s, skipping", msg.message_type)
                return None

    @staticmethod
    def _convert_tool_call(msg: PlatformMessage) -> Any:
        """Convert a tool_call message to ACP ToolCallStart.

        Falls back to text if the content can't be parsed as a tool call.
        """
        parsed = parse_tool_call(msg.content)
        if parsed is None:
            logger.warning(
                "Malformed tool_call, falling back to text: %s",
                msg.content[:100],
            )
            return update_agent_message_text(msg.content)

        return start_tool_call(
            tool_call_id=parsed.tool_call_id,
            title=parsed.name,
            kind="other",
            status="in_progress",
            raw_input=parsed.args,
        )

    @staticmethod
    def _convert_tool_result(msg: PlatformMessage) -> Any:
        """Convert a tool_result message to ACP ToolCallProgress.

        Falls back to text if the content can't be parsed as a tool result.
        """
        parsed = parse_tool_result(msg.content)
        if parsed is None:
            logger.warning(
                "Malformed tool_result, falling back to text: %s",
                msg.content[:100],
            )
            return update_agent_message_text(msg.content)

        status = "failed" if parsed.is_error else "completed"
        return update_tool_call(
            tool_call_id=parsed.tool_call_id,
            status=status,
            raw_output=parsed.output,
            content=[tool_content(text_block(parsed.output))],
        )
