"""Finish a native agent loop after a successful explicit decline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.runtime import Runtime

from band.core.turn import RoundOutcome
from band.integrations.langgraph.langchain_tools import failed_result
from band.runtime.tools import TurnEffect


def terminal_decline(
    messages: Sequence[BaseMessage], effects: Mapping[str, TurnEffect]
) -> bool:
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
        outcome = RoundOutcome()
        for result in batch:
            outcome.record(
                effects.get(result.name or "", TurnEffect.OBSERVE),
                not failed_result(result),
            )
        return outcome.should_stop(complete=True)
    return False


class DeclineMiddleware(AgentMiddleware):
    def __init__(self, effects: Mapping[str, TurnEffect]) -> None:
        self.effects = dict(effects)

    @hook_config(can_jump_to=["end"])
    def before_model(
        self, state: AgentState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        if terminal_decline(state["messages"], self.effects):
            return {"jump_to": "end"}
        return None
