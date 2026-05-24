# LangGraph Adapter

[LangGraph](https://langchain-ai.github.io/langgraph/) is a framework for building stateful, multi-step agent workflows as directed graphs. It builds on LangChain and supports checkpointing, branching, and sub-graph composition.

## Install

```bash
uv add "thenvoi-sdk[langgraph]"
```

## How It Works

The adapter converts Thenvoi room history into LangChain message format, wraps Thenvoi platform tools as LangChain tools, and invokes your graph on each incoming message. Room-to-thread mapping uses the LangGraph checkpointer, so conversation state persists across messages within a room. The system prompt is injected once per room at session bootstrap.

## Quick Start

```python
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from thenvoi.adapters import LangGraphAdapter

adapter = LangGraphAdapter(
    llm=ChatOpenAI(model="gpt-5.4-mini"),
    checkpointer=InMemorySaver(),
)
```

## Usage Patterns

### Simple (recommended)

Provide an LLM and checkpointer. The adapter builds a `create_react_agent` graph for you — shown in the quick start above.

### BYOA (custom graph)

Pass a `graph_factory` that receives Thenvoi platform tools as LangChain tools. You merge them with your own and build any graph you need:

```python
from langgraph.prebuilt import create_react_agent
from thenvoi.adapters import LangGraphAdapter

def graph_factory(thenvoi_tools):
    return create_react_agent(
        model=llm,
        tools=my_tools + thenvoi_tools,
        checkpointer=checkpointer,
    )

adapter = LangGraphAdapter(graph_factory=graph_factory)
```

Your graph keeps its own tools, prompts, and structure. The adapter adds the collaboration layer: room history, participant context, and mentions are hydrated before each invocation, and platform tools like `thenvoi_send_message` and `thenvoi_lookup_peers` arrive as regular LangChain tools.

You can also pass a pre-built static graph via `graph=` instead of a factory.

## Configuration Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `llm` | `BaseChatModel` | `None` | LangChain chat model. Required for the simple pattern. |
| `checkpointer` | `BaseCheckpointSaver` | `None` | LangGraph checkpointer for state persistence. Used with `llm`. |
| `graph_factory` | `Callable[[list], Pregel]` | `None` | Function that receives Thenvoi tools and returns a graph. Advanced pattern. |
| `graph` | `Pregel` | `None` | Pre-built static graph. Alternative to `graph_factory`. |
| `custom_section` | `str` | `""` | Appended to the SDK's base system prompt. |
| `additional_tools` | `list` | `None` | Extra LangChain tools merged with platform tools. |
| `recursion_limit` | `int` | `50` | LangGraph recursion limit per invocation. |
| `features` | `AdapterFeatures` | `None` | Capabilities and emit options. |
| `history_converter` | `LangChainHistoryConverter` | auto | Override the default history converter. |
| `prompt_template` | `str` | `"default"` | System prompt template name. |

## Capabilities and Emit

| Feature | Supported |
|---------|-----------|
| `Capability.CONTACTS` | Yes |
| `Capability.MEMORY` | Yes |
| `Emit.EXECUTION` | - |
| `Emit.THOUGHTS` | - |
| `Emit.TASK_EVENTS` | - |

LangGraph does not support any emit options. The adapter does not emit tool execution, thought, or task lifecycle events to the platform. The model can still send events organically via `thenvoi_send_event`.

## Custom Tools

LangGraph accepts native LangChain tools through `additional_tools`:

```python
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"Sunny, 22°C in {city}"

adapter = LangGraphAdapter(
    llm=llm,
    checkpointer=checkpointer,
    additional_tools=[get_weather],
)
```

### Sub-Graph Delegation

Use `graph_as_tool()` to wrap a standalone LangGraph as a tool for the main agent:

```python
from thenvoi.integrations.langgraph import graph_as_tool

calculator_tool = graph_as_tool(
    create_calculator_graph(),
    name="calculator",
    description="Evaluates math expressions",
)

adapter = LangGraphAdapter(
    llm=llm,
    checkpointer=checkpointer,
    additional_tools=[calculator_tool],
)
```

## Examples

See [examples/langgraph/](../../examples/langgraph/) for runnable scripts.

| File | Description |
|------|-------------|
| `01_simple_agent.py` | Minimal setup with LLM + platform tools |
| `02_custom_tools.py` | Calculator and weather tools via `additional_tools` |
| `03_custom_personality.py` | Custom personality via `custom_section` |
| `04_calculator_as_tool.py` | Delegates math to calculator sub-graph |
| `05_rag_as_tool.py` | Delegates research to RAG sub-graph |
| `06_delegate_to_sql_agent.py` | Delegates database queries to SQL expert |
