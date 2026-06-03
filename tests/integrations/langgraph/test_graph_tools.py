"""Tests for wrapping LangGraph graphs as LangChain tools."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from thenvoi.integrations.langgraph.graph_tools import graph_as_tool


@pytest.mark.asyncio
async def test_graph_as_tool_invokes_subgraph_with_isolated_thread() -> None:
    graph = AsyncMock()
    graph.ainvoke = AsyncMock(return_value={"answer": "done"})
    wrapped = graph_as_tool(
        graph=graph,
        name="research",
        description="Research a topic",
        input_schema={"query": "Question to answer"},
        result_formatter=lambda state: state["answer"],
    )

    result = await wrapped.ainvoke(
        {"query": "hello"},
        config={
            "configurable": {"thread_id": "room-123", "tenant": "demo"},
            "tags": ["parent-run"],
        },
    )

    assert result == "done"
    graph.ainvoke.assert_awaited_once()
    graph_input, graph_config = graph.ainvoke.await_args.args
    assert graph_input == {"query": "hello"}
    thread_id = graph_config["configurable"]["thread_id"]
    assert thread_id.startswith("subgraph:research:room-123:")
    assert graph_config["configurable"]["tenant"] == "demo"
    assert graph_config["tags"] == ["parent-run"]


@pytest.mark.asyncio
async def test_graph_as_tool_can_share_main_thread() -> None:
    graph = AsyncMock()
    graph.ainvoke = AsyncMock(return_value={"answer": "done"})
    wrapped = graph_as_tool(
        graph=graph,
        name="research",
        description="Research a topic",
        input_schema={"query": "Question to answer"},
        result_formatter=lambda state: state["answer"],
        isolate_thread=False,
    )

    await wrapped.ainvoke(
        {"query": "hello"},
        config={
            "configurable": {"thread_id": "room-123", "tenant": "demo"},
            "tags": ["parent-run"],
            "metadata": {"room": "room-123"},
            "recursion_limit": 25,
        },
    )

    _graph_input, graph_config = graph.ainvoke.await_args.args
    assert graph_config["configurable"] == {"thread_id": "room-123", "tenant": "demo"}
    assert graph_config["tags"] == ["parent-run"]
    assert graph_config["metadata"] == {"room": "room-123"}
    assert graph_config["recursion_limit"] == 25


@pytest.mark.asyncio
async def test_graph_as_tool_formats_dict_results_as_json() -> None:
    graph = AsyncMock()
    graph.ainvoke = AsyncMock(return_value={"answer": "done", "score": 1})
    wrapped = graph_as_tool(
        graph=graph,
        name="research",
        description="Research a topic",
        input_schema={"query": "Question to answer"},
        result_formatter=lambda state: {"answer": state["answer"]},
    )

    result = await wrapped.ainvoke(
        {"query": "hello"},
        config={"configurable": {"thread_id": "room-123"}},
    )

    assert result == '{\n  "answer": "done"\n}'


def test_graph_as_tool_validates_required_metadata() -> None:
    graph = AsyncMock()

    with pytest.raises(ValueError, match="Tool name is required"):
        graph_as_tool(graph, "", "description", {"query": "Question"})

    with pytest.raises(ValueError, match="Tool description is required"):
        graph_as_tool(graph, "research", "", {"query": "Question"})

    with pytest.raises(ValueError, match="Input schema is required"):
        graph_as_tool(graph, "research", "description", {})


def test_graph_as_tool_exposes_input_schema() -> None:
    graph = AsyncMock()
    wrapped = graph_as_tool(
        graph=graph,
        name="research",
        description="Research a topic",
        input_schema={"query": "Question to answer"},
    )

    assert wrapped.name == "research"
    assert wrapped.args == {
        "query": {"description": "Question to answer", "title": "Query"}
    }
