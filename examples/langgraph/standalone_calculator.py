"""
Standalone calculator graph - completely independent of Thenvoi.

This is a simple LangGraph that performs mathematical calculations.
It can be imported and used as a tool in any agent.
"""

from typing import TypedDict, Literal
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver


class CalculatorState(TypedDict):
    """State for the calculator graph."""

    operation: Literal["add", "subtract", "multiply", "divide"]
    a: float
    b: float
    result: float
    error: str | None


async def calculate_node(state: CalculatorState) -> dict:
    """Performs the calculation."""
    op = state["operation"]
    a = state["a"]
    b = state["b"]

    if op == "add":
        result = a + b
    elif op == "subtract":
        result = a - b
    elif op == "multiply":
        result = a * b
    elif op == "divide":
        if b == 0:
            raise ValueError("Cannot divide by zero")
        result = a / b
    else:
        raise ValueError(f"Unknown operation: {op}")

    return {"result": result, "error": None}


def create_calculator_graph():
    """
    Creates a compiled calculator graph.

    This is a standalone graph that knows nothing about Thenvoi.
    It simply takes input parameters and returns results.

    Returns:
        Compiled LangGraph that can perform calculations
    """
    graph = StateGraph(CalculatorState)

    # Add the calculation node
    graph.add_node("calculate", calculate_node)

    # Simple flow: start -> calculate -> end
    graph.add_edge(START, "calculate")
    graph.add_edge("calculate", END)

    # Compile with checkpointer for state management
    return graph.compile(checkpointer=InMemorySaver())


# Export for easy import
if __name__ == "__main__":
    import asyncio
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    async def test():
        """Test the calculator graph standalone."""
        calc = create_calculator_graph()

        # Test addition
        result = await calc.ainvoke(
            {"operation": "add", "a": 5, "b": 3},
            {"configurable": {"thread_id": "test-1"}},
        )
        logger.info("5 + 3 = %s", result["result"])

        # Test multiplication
        result = await calc.ainvoke(
            {"operation": "multiply", "a": 7, "b": 6},
            {"configurable": {"thread_id": "test-2"}},
        )
        logger.info("7 * 6 = %s", result["result"])

        # Test division by zero (should raise error)
        try:
            result = await calc.ainvoke(
                {"operation": "divide", "a": 10, "b": 0},
                {"configurable": {"thread_id": "test-3"}},
            )
        except ValueError as e:
            logger.info("Division by zero error: %s", e)

    asyncio.run(test())
