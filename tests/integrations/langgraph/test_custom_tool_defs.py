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


def test_adapter_splits_custom_tool_defs_from_native_tools() -> None:
    """Deliberately amended for INT-994: tuples are no longer converted once at
    init — they are held raw on _custom_tool_defs and converted per turn, so
    the conversion can bind that turn's ExecutionContext. Native LangChain
    tools still pass through at init.
    """

    @lc_tool
    def native(x: str) -> str:
        """A ready-made LangChain tool."""
        return x

    adapter = LangGraphAdapter(
        graph_factory=lambda tools: MagicMock(),
        additional_tools=[(EchoInput, echo), native],
    )

    assert adapter._custom_tool_defs == [(EchoInput, echo)]
    assert adapter.additional_tools == [native]
    assert not any(isinstance(t, tuple) for t in adapter.additional_tools)


async def test_converter_threads_ctx_to_opt_in_handler() -> None:
    """INT-994: the converter binds the given ctx into the tool closure, and a
    two-param handler receives it."""
    received = []

    async def probe(args: EchoInput, ctx) -> str:
        received.append(ctx)
        return f"echo:{args.text}"

    sentinel_ctx = object()
    (converted,) = custom_tool_defs_to_langchain([(EchoInput, probe)], ctx=sentinel_ctx)

    assert await converted.ainvoke({"text": "hi"}) == "echo:hi"
    assert received == [sentinel_ctx]


async def test_converter_defaults_to_no_ctx() -> None:
    """Without a bound ctx the opt-in handler still runs, receiving None."""
    received = []

    async def probe(args: EchoInput, ctx) -> str:
        received.append(ctx)
        return "ok"

    (converted,) = custom_tool_defs_to_langchain([(EchoInput, probe)])

    assert await converted.ainvoke({"text": "hi"}) == "ok"
    assert received == [None]
