"""LangGraph accepts the SDK's portable custom-tool form (``CustomToolDef``).

Every adapter takes custom tools as ``(InputModel, handler)`` tuples; LangGraph
historically took only ready-made LangChain tools, so a bare tuple reached
LangChain and raised "the first argument must be a string or a callable ... Got
<class 'tuple'>". These tests cover the converter and the adapter's one-time
normalization (tuples -> StructuredTools; native LangChain tools pass through).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.tools import StructuredTool
from langchain_core.tools import tool as lc_tool
from pydantic import BaseModel, Field

from band.adapters.langgraph import LangGraphAdapter
from band.integrations.langgraph.langchain_tools import custom_tool_defs_to_langchain


class EchoInput(BaseModel):
    """Echo the given text."""

    text: str = Field(description="text to echo")


def echo(args: EchoInput) -> str:
    return f"echo:{args.text}"


async def test_converter_produces_a_runnable_structured_tool() -> None:
    tools = custom_tool_defs_to_langchain([(EchoInput, echo)])

    assert len(tools) == 1
    converted = tools[0]
    assert isinstance(converted, StructuredTool)
    assert converted.name == "echo"  # get_custom_tool_name(EchoInput)
    assert converted.args_schema is EchoInput
    # The wrapper validates args and runs the handler.
    assert await converted.ainvoke({"text": "hi"}) == "echo:hi"


async def test_converter_reports_bad_args_to_the_model() -> None:
    (converted,) = custom_tool_defs_to_langchain([(EchoInput, echo)])
    # A validation error is returned as a string (fed back to the LLM), not raised.
    result = await converted.ainvoke({"wrong": "field"})
    assert isinstance(result, str) and "text" in result


def test_adapter_normalizes_custom_tool_defs_and_passes_native_through() -> None:
    @lc_tool
    def native(x: str) -> str:
        """A ready-made LangChain tool."""
        return x

    # Advanced pattern (graph_factory) keeps additional_tools on the instance, so we
    # can assert the normalization. A CustomToolDef tuple + a native tool go in.
    adapter = LangGraphAdapter(
        graph_factory=lambda tools: MagicMock(),
        additional_tools=[(EchoInput, echo), native],
    )

    assert len(adapter.additional_tools) == 2
    assert not any(isinstance(t, tuple) for t in adapter.additional_tools)
    names = {getattr(t, "name", None) for t in adapter.additional_tools}
    assert names == {"echo", "native"}
