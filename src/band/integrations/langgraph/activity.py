"""Source-ordered native LangChain tool events, excluding SDK execution wrappers."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from band.core.protocols import AgentToolsProtocol
from band.runtime.toolactivity import (
    WRAPPED_TOOL_ACTIVITY,
    end_activity,
    start_native_tool_activity,
)


class LangChainToolEvent(StrEnum):
    """Tool lifecycle events consumed by activity and narration."""

    START = "on_tool_start"
    END = "on_tool_end"
    ERROR = "on_tool_error"


async def observe_tool_event(
    event: Mapping[str, Any], tools: AgentToolsProtocol, operations: dict[str, object]
) -> None:
    run_id = event.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return
    metadata = event.get("metadata") or {}
    if metadata.get(WRAPPED_TOOL_ACTIVITY):
        return
    match event.get("event"):
        case LangChainToolEvent.START if run_id not in operations:
            token = operations[run_id] = object()
            await start_native_tool_activity(tools, token, event.get("name"))
        case LangChainToolEvent.END | LangChainToolEvent.ERROR:
            if token := operations.pop(run_id, None):
                await end_activity(tools, token)
