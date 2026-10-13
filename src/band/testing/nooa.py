"""Scripted NOOA model turns for offline adapter tests.

NOOA's default CodeAct strategy gives the model two tools, ``execute_python``
and ``return_result``. These helpers script those calls on NOOA's own
``FakeLLMClient``, so tests drive the real agent loop without inference.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast
from uuid import uuid4

from nooa import Agent
from nooa.unifiedllm import FakeLLMClient, LLMResponse, ToolCall, UnifiedLLM

from band.adapters.nooa import NooaRoomAgent

EXECUTE_PYTHON = "execute_python"
RETURN_RESULT = "return_result"


def tool_call_turn(
    name: str,
    arguments: dict[str, Any],
    *,
    usage: dict[str, int] | None = None,
    reasoning: str | None = None,
) -> LLMResponse:
    """A model turn that calls one CodeAct tool, with optional usage and reasoning."""
    call_id = f"call-{uuid4().hex[:8]}"
    encoded = json.dumps(arguments)
    return LLMResponse(
        raw_response=None,
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=encoded)],
        finish_reason="tool_calls",
        assistant_message={
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": encoded},
                }
            ],
        },
        reasoning=reasoning,
        usage=usage,
    )


def code_turn(
    code: str, *, usage: dict[str, int] | None = None, reasoning: str | None = None
) -> LLMResponse:
    """A model turn that runs *code* in the agent's Python session."""
    return tool_call_turn(
        EXECUTE_PYTHON, {"code": code}, usage=usage, reasoning=reasoning
    )


def result_turn(
    value: Any = None, *, usage: dict[str, int] | None = None
) -> LLMResponse:
    """A model turn that ends the agentic method with *value*."""
    return tool_call_turn(RETURN_RESULT, {"value": value}, usage=usage)


def room_call(tool_name: str, arguments: dict[str, Any] | None = None) -> str:
    """Python the model would write to call a tool on ``self.room``."""
    return f"await self.room.{tool_name}(**{arguments or {}!r})"


def scripted_llm(*cells: str) -> FakeLLMClient:
    """A model that runs each cell in turn, then returns ``None``.

    Every call to an agentic method consumes its own cells and final result,
    so script one method call per ``scripted_llm`` unless the cells are meant
    to span several calls.
    """
    return FakeLLMClient([*(code_turn(cell) for cell in cells), result_turn()])


def room_agent_class(
    llm: UnifiedLLM,
    *,
    base: type[Agent] = NooaRoomAgent,
    name: str = "ScriptedRoomAgent",
    doc: str = "You are a scripted test agent.",
    namespace: Mapping[str, Any] | None = None,
    **settings: Any,
) -> type[NooaRoomAgent]:
    """A subclass of *base* (a room agent by default) that runs on *llm*.

    *namespace* adds class attributes and methods; *settings* are NOOA's class
    keywords, such as ``execution=``. The class lives in the room agent's own
    module, so the model's execution context lists nothing from test code.
    """
    body = {"__doc__": doc, "__module__": NooaRoomAgent.__module__, **(namespace or {})}
    return cast(
        type[NooaRoomAgent],
        type(base)(name, (base,), body, llm=llm, **settings),
    )
