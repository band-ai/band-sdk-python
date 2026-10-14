"""Finish a native agent loop after a successful explicit decline."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.runtime import Runtime

from band.runtime.tools import BandTool
from band.runtime.tools.schema import is_failed_tool_output


def failed_result(message: ToolMessage) -> bool:
    content = message.content
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            pass
    return message.status != "success" or is_failed_tool_output(content)


def terminal_decline(messages: Sequence[BaseMessage]) -> bool:
    batch: list[ToolMessage] = []
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            batch.append(message)
            continue
        if not isinstance(message, AIMessage) or not batch:
            return False
        expected = {call["id"] for call in message.tool_calls}
        if {result.tool_call_id for result in batch} != expected:
            return False
        return not any(failed_result(result) for result in batch) and any(
            result.name == BandTool.NO_REPLY for result in batch
        )
    return False


class DeclineMiddleware(AgentMiddleware):
    @hook_config(can_jump_to=["end"])
    def before_model(
        self, state: AgentState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        if terminal_decline(state["messages"]):
            return {"jump_to": "end"}
        return None
