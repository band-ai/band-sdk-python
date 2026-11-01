"""Source-ordered native LangChain tool events, excluding SDK execution wrappers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from band.core.protocols import AgentToolsProtocol
from band.runtime.toolactivity import (
    WRAPPED_TOOL_ACTIVITY,
    end_activity,
    start_native_tool_activity,
)


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
        case "on_tool_start" if run_id not in operations:
            token = operations[run_id] = object()
            await start_native_tool_activity(tools, token, event.get("name"))
        case "on_tool_end" | "on_tool_error":
            if token := operations.pop(run_id, None):
                await end_activity(tools, token)
