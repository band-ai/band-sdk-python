from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class _CalcState(TypedDict):
    result: int


def _add(state: _CalcState) -> _CalcState:
    return {"result": 0}


def create_calculator_graph():
    graph = StateGraph(_CalcState)
    graph.add_node("add", _add)
    graph.add_edge(START, "add")
    graph.add_edge("add", END)
    return graph.compile()
