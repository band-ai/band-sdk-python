"""Utilities for wrapping LangGraph graphs as tools."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated, Any, Callable, cast
from pydantic import create_model
from langchain_core.tools import BaseTool, InjectedToolArg, tool
from langchain_core.runnables import RunnableConfig
from langgraph.pregel import Pregel

logger = logging.getLogger(__name__)


def graph_as_tool(
    graph: Pregel,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    result_formatter: Callable[[dict[str, Any]], Any] | None = None,
    isolate_thread: bool = True,
) -> BaseTool:
    """
    Wrap a LangGraph graph as a LangChain tool.

    This allows a main agent to invoke a specialized graph as a tool,
    enabling hierarchical agent architectures where the main agent
    delegates complex tasks to specialized subgraphs.

    Args:
        graph: Compiled LangGraph graph (Pregel instance)
        name: Tool name (must be unique, lowercase with underscores)
        description: Description for the LLM to understand when to use this tool
        input_schema: Dict describing expected input parameters that map to the graph's state.
                     Format: {"param_name": "description", ...}

                     **Important**: The keys must match the graph's state fields!

                     Examples:
                     - For custom state: {"operation": "add/subtract/multiply/divide", "a": "first number", "b": "second number"}
                     - For MessagesState: {"messages": "List of message dicts with role and content"}

                     The LLM will be instructed to provide values for these parameters,
                     which are then passed directly to graph.ainvoke(kwargs)
        result_formatter: Optional function to transform the graph's final state into a useful result
                         for the main agent. Signature: (state: dict) -> Any

                         **Why use this?**
                         Graphs return their entire final state, which often contains internal details
                         the main agent doesn't need. The formatter extracts just what's relevant.

                         **Examples:**
                         - Calculator: `lambda state: f"Result: {state['result']}"` extracts just the answer
                         - RAG/Chat: `lambda state: state["messages"][-1].content` extracts the final response
                         - Complex: `lambda state: {"answer": state["answer"], "confidence": state["score"]}`

                         **If None:** Returns str(state), which includes all internal state.
                         This is verbose but can be useful for debugging.
        isolate_thread: If True (default), each tool invocation gets a fresh conversation.
                       If False, the subgraph remembers conversation history across invocations
                       within the same room.

                       **When to use True (isolated):**
                       - Each question should be independent
                       - Calculator, one-off tasks
                       - You don't want the subgraph remembering previous calls

                       **When to use False (shared memory):**
                       - Multi-turn conversations with the subgraph
                       - RAG where follow-up questions reference previous answers
                       - You want "conversation memory" for the subgraph

                       Default: True (most common - stateless tool behavior)

    Returns:
        LangChain tool function that can be added to agent's tools

    Example:
        >>> from examples.langgraph.standalone_calculator import create_calculator_graph
        >>> calculator = create_calculator_graph()
        >>> calc_tool = graph_as_tool(
        ...     graph=calculator,
        ...     name="calculator",
        ...     description="Performs mathematical operations",
        ...     input_schema={
        ...         "operation": "add, subtract, multiply, or divide",
        ...         "a": "First number",
        ...         "b": "Second number"
        ...     },
        ...     result_formatter=lambda state: f"Result: {state['result']}"
        ... )
        >>> adapter = LangGraphAdapter(
        ...     llm=llm,
        ...     checkpointer=checkpointer,
        ...     additional_tools=[calc_tool],
        ... )

    Thread Isolation Explained:
        The subgraph has its own checkpointer (created when the graph was compiled).
        The question is: should the subgraph remember conversation history between tool calls?

        **isolate_thread=True (default):**
        - Each invocation gets unique thread_id: "subgraph:{name}:{main_thread}:{uuid}"
        - Subgraph starts fresh each time (no memory between calls)
        - Like calling a stateless function
        - Use for: calculator, one-off queries, independent tasks

        **isolate_thread=False:**
        - Uses main agent's thread_id for the subgraph too
        - Subgraph remembers its conversation history across invocations in the same room
        - Follow-up questions can reference previous answers
        - Use for: RAG with follow-ups, conversational sub-agents

        Example: Main agent in room "abc123" calls tool twice
        - Isolated (True): Call 1 uses "subgraph:research:abc123:x1y2", Call 2 uses "subgraph:research:abc123:a3b4" → no memory
        - Shared (False): Both calls use "abc123" → subgraph remembers Call 1 when handling Call 2

    Error Handling:
        Errors from the subgraph are allowed to bubble up to the caller.
        This ensures the main agent can see and handle errors appropriately.
    """

    # Validate inputs
    if not name:
        raise ValueError("Tool name is required")
    if not description:
        raise ValueError("Tool description is required")
    if not input_schema:
        raise ValueError("Input schema is required")

    # Create Pydantic model for the input schema
    # This ensures LangChain knows what parameters the tool expects
    from pydantic import Field

    field_definitions = {}
    for param_name, param_desc in input_schema.items():
        field_definitions[param_name] = (Any, Field(description=param_desc))

    # Type checkers can't verify create_model's **kwargs pattern - this is valid pydantic usage
    InputModel = create_model(f"{name.title()}Input", **field_definitions)  # type: ignore[call-overload]

    # Create schema description for the tool docstring
    schema_desc = "\n".join([f"  - {k}: {v}" for k, v in input_schema.items()])
    full_description = f"{description}\n\nParameters:\n{schema_desc}"

    # Create the wrapper function with the proper name
    async def graph_tool_wrapper(
        config: Annotated[RunnableConfig, InjectedToolArg], **kwargs
    ) -> str:
        # LangChain injects RunnableConfig for tool calls when the wrapper accepts
        # a config parameter. This carries the main room's thread_id.
        main_thread_id = config.get("configurable", {}).get("thread_id")

        logger.debug("[%s] Invoking subgraph with inputs: %s", name, kwargs)
        logger.debug("[%s] Main thread_id: %s", name, main_thread_id)

        # Determine thread_id for subgraph execution
        if isolate_thread:
            invocation_id = uuid.uuid4().hex[:8]
            subgraph_thread = f"subgraph:{name}:{main_thread_id}:{invocation_id}"
            logger.debug("[%s] Using isolated thread: %s", name, subgraph_thread)
        else:
            subgraph_thread = main_thread_id
            logger.debug(
                "[%s] Using shared thread_id - subgraph will remember across invocations",
                name,
            )

        # Invoke the subgraph - let errors bubble up
        # Even though we use ainvoke, the main agent's astream_events will still
        # see all the subgraph's internal events because of how LangGraph works
        subgraph_config = cast(
            RunnableConfig,
            {
                **config,
                "configurable": {
                    **config.get("configurable", {}),
                    "thread_id": subgraph_thread,
                },
            },
        )
        result = await graph.ainvoke(
            kwargs,  # Pass user parameters to subgraph
            subgraph_config,
        )

        logger.debug("[%s] Subgraph execution completed", name)
        logger.debug("[%s] Raw result: %s", name, result)

        # Format the result for the main agent
        # The subgraph returns its entire final state, but the main agent usually
        # only needs a specific piece of information (e.g., just the answer, not
        # all intermediate messages or internal variables)
        if result_formatter:
            formatted = result_formatter(result)
            # Convert to string if not already (tools must return strings)
            if isinstance(formatted, (dict, list)):
                formatted = json.dumps(formatted, indent=2)
            return str(formatted)
        else:
            # No formatter: return entire state as string
            # Useful for debugging but verbose for production
            return str(result)

    # Set the function name and docstring for the @tool decorator
    graph_tool_wrapper.__name__ = name
    graph_tool_wrapper.__doc__ = full_description

    # Apply the @tool decorator with the input schema
    return tool(graph_tool_wrapper, args_schema=InputModel)
