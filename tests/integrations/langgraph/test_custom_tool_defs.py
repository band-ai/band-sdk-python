"""LangGraph accepts the SDK's portable custom-tool form (``CustomToolDef``).

Every adapter takes custom tools as ``(InputModel, handler)`` tuples; LangGraph
historically took only ready-made LangChain tools, so a bare tuple reached
LangChain and raised "the first argument must be a string or a callable ... Got
<class 'tuple'>". These tests cover the converter and the adapter's per-turn
conversion (tuples -> StructuredTools bound to the room's turn; native LangChain
tools pass through).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.tools import StructuredTool
from langchain_core.tools import tool as lc_tool
from pydantic import BaseModel, Field

from band.adapters.langgraph import LangGraphAdapter
from band.core.turn import Turn
from band.core.types import PlatformMessage
from band.integrations.langgraph.langchain_tools import custom_tool_def_to_langchain
from band.testing import FakeAgentTools, reported_failures
from tests.framework_conformance.turnprobes import CUSTOM_TOOL_DECLARATIONS


class EchoInput(BaseModel):
    """Echo the given text."""

    text: str = Field(description="text to echo")


def echo(args: EchoInput) -> str:
    return f"echo:{args.text}"


async def test_converter_produces_a_runnable_structured_tool() -> None:
    converted = custom_tool_def_to_langchain((EchoInput, echo), turn=None)

    assert isinstance(converted, StructuredTool)
    assert converted.name == "echo"  # get_custom_tool_name(EchoInput)
    assert converted.args_schema is EchoInput
    # The wrapper validates args and runs the handler.
    assert await converted.ainvoke({"text": "hi"}) == "echo:hi"


async def test_converter_reports_bad_args_to_the_model() -> None:
    converted = custom_tool_def_to_langchain((EchoInput, echo), turn=None)
    # A validation error is returned as a string (fed back to the LLM), not raised.
    result = await converted.ainvoke({"wrong": "field"})
    assert isinstance(result, str) and "text" in result


def test_adapter_converts_custom_tool_defs_and_passes_native_through() -> None:
    @lc_tool
    def native(x: str) -> str:
        """A ready-made LangChain tool."""
        return x

    adapter = LangGraphAdapter(
        graph_factory=lambda tools: None,
        additional_tools=[(EchoInput, echo), native],
    )

    turn_tools = adapter._additional_tools_for_turn(Turn())

    assert not any(isinstance(t, tuple) for t in turn_tools)
    assert {t.name for t in turn_tools} == {"echo", "native"}


class FileInput(BaseModel):
    """File the report."""

    note: str


class CallsToolGraph:
    """A graph whose one step calls the named tool, as a real agent graph would."""

    def __init__(self, turn_tools: list[Any], tool_name: str) -> None:
        (self._tool,) = [t for t in turn_tools if t.name == tool_name]

    async def astream_events(
        self, graph_input: dict[str, Any], **kwargs: Any
    ) -> AsyncIterator[dict[str, Any]]:
        await self._tool.ainvoke({"note": "go"})
        return
        yield


@pytest.mark.parametrize(("declare", "complete"), CUSTOM_TOOL_DECLARATIONS)
async def test_custom_tool_records_its_effect_on_the_room_turn(
    declare: Callable[..., Any], complete: bool
) -> None:
    @declare
    def file_report(args: FileInput) -> str:
        return "filed"

    tools = FakeAgentTools(room_id="room-1")
    adapter = LangGraphAdapter(
        graph_factory=lambda turn_tools: CallsToolGraph(turn_tools, "file"),
        additional_tools=[(FileInput, file_report)],
    )
    await adapter.on_started("TestBot", "Test bot")

    await adapter.on_message(
        msg=PlatformMessage(
            id="msg-1",
            room_id="room-1",
            content="file it",
            sender_id="user-1",
            sender_type="User",
            sender_name="Alice",
            message_type="text",
            metadata={},
            created_at=datetime.now(UTC),
        ),
        tools=tools,
        history=[],
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=False,
        room_id="room-1",
    )

    assert reported_failures(tools) == []
    assert tools.turn.complete is complete
