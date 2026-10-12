"""Scripted NOOA model turns for offline adapter tests.

NOOA's default CodeAct strategy gives the model two tools, ``execute_python``
and ``return_result``. These helpers script those calls on NOOA's own
``FakeLLMClient``, so tests drive the real agent loop without inference.
"""

from __future__ import annotations

import json
from typing import Any, cast
from uuid import uuid4

try:
    from nooa.unifiedllm import FakeLLMClient, LLMResponse, ToolCall
except ModuleNotFoundError as error:
    # ModuleNotFoundError keeps the missing module's name, so a venv without the
    # extra can still tell "absent optional dependency" from a broken import.
    raise ModuleNotFoundError(
        "NOOA dependencies not installed (NOOA supports Python 3.12 and 3.13 on "
        "Linux and macOS). Install with: uv add band-sdk[nooa]",
        name=error.name,
    ) from error

from band.adapters.nooa import NooaRoomAgent

EXECUTE_PYTHON = "execute_python"
RETURN_RESULT = "return_result"


def tool_call_turn(
    name: str, arguments: dict[str, Any], *, usage: dict[str, int] | None = None
) -> LLMResponse:
    """A model turn that calls one CodeAct tool, reporting *usage* if given."""
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
        reasoning=None,
        usage=usage,
    )


def code_turn(code: str, *, usage: dict[str, int] | None = None) -> LLMResponse:
    """A model turn that runs *code* in the agent's Python session."""
    return tool_call_turn(EXECUTE_PYTHON, {"code": code}, usage=usage)


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


def scripted_room_agent(llm: FakeLLMClient) -> type[NooaRoomAgent]:
    """A ``NooaRoomAgent`` subclass driven by a scripted *llm*."""
    return cast(
        type[NooaRoomAgent],
        type(NooaRoomAgent)(
            "ScriptedRoomAgent",
            (NooaRoomAgent,),
            {"__doc__": "You are a scripted test agent.", "__module__": __name__},
            llm=llm,
        ),
    )
