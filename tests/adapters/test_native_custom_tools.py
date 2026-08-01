"""Custom-tool behaviour the two native adapters must share.

Anthropic and Gemini drive the same ``NativeToolLoopBackend``, so a custom
tool has to behave identically under both. These tests run a real turn
against each, replacing only the provider's SDK client.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field

from band.adapters.anthropic import AnthropicAdapter
from band.adapters.gemini import GeminiAdapter
from band.core.tools import FunctionTool
from band.core.types import AdapterFeatures, Emit, PlatformMessage
from band.runtime.tools import ToolCallOutcome
from tests.modelclients import (
    ScriptedAnthropicClient,
    ScriptedGeminiClient,
    anthropic_reply,
    gemini_reply,
)

CUSTOM_TOOL_NAME = "greet"  # GreetInput -> "greet" (see get_custom_tool_name)


class GreetInput(BaseModel):
    """A custom tool whose argument is required, so bad args fail validation."""

    name: str = Field(description="Who to greet")


class Recipient(BaseModel):
    """A model the handler builds itself, from data the model never supplied."""

    email: str


async def greet(name: str) -> str:
    return f"hello {name}"


async def greet_strictly(name: str) -> str:
    """Raise ``ValidationError`` from inside the handler, not from its arguments.

    ``execute_custom_tool`` only validates the *arguments*, so this is the one
    way a raw ``ValidationError`` still reaches the executor.
    """
    Recipient.model_validate({"name": name})
    return f"hello {name}"


def _custom_tool() -> FunctionTool:
    return FunctionTool.from_custom_tool_def((GreetInput, greet))


def _strict_custom_tool() -> FunctionTool:
    return FunctionTool.from_custom_tool_def((GreetInput, greet_strictly))


def _tools() -> MagicMock:
    tools = MagicMock()
    tools.get_anthropic_tool_schemas = MagicMock(return_value=[])
    tools.get_openai_tool_schemas = MagicMock(return_value=[])
    tools.send_message = AsyncMock(return_value={"status": "sent"})
    tools.send_event = AsyncMock(return_value={"status": "sent"})
    tools.execute_tool_call_structured = AsyncMock(
        return_value=ToolCallOutcome(value={"status": "success"}, ok=True)
    )
    return tools


def _message() -> PlatformMessage:
    return PlatformMessage(
        id="msg-1",
        room_id="room-1",
        content="greet someone",
        sender_id="user-1",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


def _narrating() -> AdapterFeatures:
    """Emit execution events, so the tool result the model saw is observable."""
    return AdapterFeatures(emit={Emit.EXECUTION})


def _build_anthropic(tool: FunctionTool, arguments: dict[str, Any]) -> AnthropicAdapter:
    adapter = AnthropicAdapter(additional_tools=[tool], features=_narrating())
    adapter.client = ScriptedAnthropicClient(
        [
            anthropic_reply(tool_calls=[(CUSTOM_TOOL_NAME, arguments)]),
            anthropic_reply("understood"),
        ]
    )
    return adapter


def _build_gemini(tool: FunctionTool, arguments: dict[str, Any]) -> GeminiAdapter:
    adapter = GeminiAdapter(
        provider_key="test-key", additional_tools=[tool], features=_narrating()
    )
    adapter.client = ScriptedGeminiClient(
        [
            gemini_reply(tool_calls=[(CUSTOM_TOOL_NAME, arguments)]),
            gemini_reply("understood"),
        ]
    )
    return adapter


ADAPTERS = [
    pytest.param(_build_anthropic, id="anthropic"),
    pytest.param(_build_gemini, id="gemini"),
]


async def _run_turn(adapter: Any, tools: Any) -> None:
    await adapter.on_message(
        msg=_message(),
        tools=tools,
        history=[],
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )


def _narrated_results(tools: Any) -> list[str]:
    return [
        call.kwargs["content"]
        for call in tools.send_event.await_args_list
        if call.kwargs.get("message_type") == "tool_result"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("build", ADAPTERS)
async def test_bad_custom_tool_arguments_are_reported_to_the_model(build) -> None:
    """Arguments the tool's model rejects reach the model as a result.

    ``execute_custom_tool`` renders this one itself, so it arrives on the
    generic failure path — what matters is that the text names the tool and
    the field, and that the loop runs another round.
    """
    adapter = build(_custom_tool(), {})
    tools = _tools()

    await _run_turn(adapter, tools)

    assert adapter.client.call_count == 2, (
        "the loop should have run a second round with the validation result"
    )
    results = _narrated_results(tools)
    assert results, "the failed custom tool was never narrated as a result"
    assert f"Invalid arguments for {CUSTOM_TOOL_NAME}" in results[0]
    assert "name" in results[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("build", ADAPTERS)
async def test_a_handler_raising_validation_error_does_not_kill_the_turn(
    build,
) -> None:
    """A ``ValidationError`` from inside the handler is a failed call, not a crash.

    Argument validation is already rendered by ``execute_custom_tool``, so this
    is the only path that still reaches the executor's ``ValidationError``
    branch. It used to re-raise unless the adapter supplied a formatter —
    which one of the two did — so the turn's survival depended on which
    provider was driving.
    """
    adapter = build(_strict_custom_tool(), {"name": "Alice"})
    tools = _tools()

    await _run_turn(adapter, tools)

    assert adapter.client.call_count == 2, "the turn died instead of reporting"
    results = _narrated_results(tools)
    assert results, "the failed handler was never narrated as a result"
    assert "email" in results[0], "the model should see which field failed"
