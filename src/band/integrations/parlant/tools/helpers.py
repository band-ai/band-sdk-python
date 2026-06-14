"""Shared helpers for Parlant tools: session lookup, error handling, serialization."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from band.integrations.parlant.tools.registry import get_session_tools

logger = logging.getLogger(__name__)


def normalize(result: Any) -> Any:
    """Normalize Fern/Pydantic models to dictionaries when possible."""
    return result.model_dump() if hasattr(result, "model_dump") else result


def build_helpers(ToolResult: Any) -> SimpleNamespace:
    """Build helpers that need Parlant's runtime ToolResult type."""

    async def execute(
        context: Any,
        tool_name: str,
        verb: str,
        body: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] %s: No tools available for session %s",
                tool_name,
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            return await body(tools)
        except Exception as e:
            logger.error("[Parlant Tool] Error %s: %s", verb, e, exc_info=True)
            return ToolResult(data=f"Error {verb}: {e}")

    def json_result(result: Any) -> Any:
        return ToolResult(data=json.dumps(normalize(result), default=str))

    def require_memory_id(memory_id: str) -> Any | None:
        if memory_id:
            return None
        return ToolResult(
            data=(
                "Error: memory_id is required. Use band_list_memories first "
                "with a content_query to find the memory_id, then retry."
            )
        )

    return SimpleNamespace(
        execute=execute,
        json_result=json_result,
        require_memory_id=require_memory_id,
    )
