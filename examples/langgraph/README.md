# LangGraph Examples for Thenvoi

This guide explains how to integrate LangGraph agents with the Thenvoi platform using the composition-based SDK.

## Prerequisites

**If running from repository:**
```bash
# From thenvoi-sdk-python/ directory
uv sync --extra langgraph
```

**If using as external library:**
```bash
uv add "git+https://github.com/thenvoi/thenvoi-sdk-python.git[langgraph]"
```

**Configuration:**
- Set `OPENAI_API_KEY` environment variable. Optionally set `OPENAI_MODEL` to override the default `gpt-4o` model.
- Configure agent credentials (see main [README](../../README.md#creating-external-agents-on-thenvoi-platform)).

---

## Quick Start

```python
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

# Create adapter with LLM and checkpointer
adapter = LangGraphAdapter(
    llm=ChatOpenAI(model="gpt-4o"),
    checkpointer=InMemorySaver(),
)

# Create and run agent
agent = Agent.create(
    adapter=adapter,
    agent_id="your-agent-id",
    api_key="your-api-key",
)
await agent.run()
```

---

## Examples

### Getting Started

| File | Description |
|------|-------------|
| `01_simple_agent.py` | **Minimal setup** - Just LLM + platform tools. Great starting point. |
| `02_custom_tools.py` | **Custom tools** - Built-in agent + calculator and weather tools using `additional_tools`. |
| `03_custom_personality.py` | Custom personality - built-in agent plus pirate behavior using `custom_section`. |

### Advanced: Delegating to Sub-Agents

| File | Description |
|------|-------------|
| `04_calculator_as_tool.py` | **Calculator sub-graph** - Delegates math to calculator sub-graph using `graph_as_tool()`. |
| `05_rag_as_tool.py` | **RAG sub-graph** - Delegates research to RAG sub-graph with vector search. |
| `06_delegate_to_sql_agent.py` | **SQL sub-agent** - Delegates database queries to SQL expert. |

### Multi-Agent and Custom Graphs

| File | Description |
|------|-------------|
| `07_tom_agent.py` | Character agent that can look up and invite another agent into the room. |
| `08_jerry_agent.py` | Paired character agent for multi-agent room demos. |
| `09_research_ops_orchestrator.py` | **Custom operations graph** - Multi-node graph with platform events, calculator delegation, and SQL delegation. |

**Supporting files:** `standalone_calculator.py`, `standalone_rag.py`, `standalone_sql_agent.py`

---

## Adding Custom Tools

```python
from langchain_core.tools import tool
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter

@tool
def my_custom_tool(query: str) -> str:
    """Does something useful."""
    return "result"

adapter = LangGraphAdapter(
    llm=ChatOpenAI(model="gpt-4o"),
    checkpointer=InMemorySaver(),
    additional_tools=[my_custom_tool],  # Your tools added here
)

agent = Agent.create(adapter=adapter, agent_id=..., api_key=...)
await agent.run()
```

---

## Wrapping a Graph as a Tool

Use `graph_as_tool()` to wrap a standalone LangGraph as a tool for the main agent:

```python
from thenvoi.integrations.langgraph import graph_as_tool

# Create a sub-graph for specialized work
calculator_graph = create_calculator_graph()

# Wrap it as a tool
calculator_tool = graph_as_tool(
    calculator_graph,
    name="calculator",
    description="Evaluates math expressions",
    input_schema={
        "operation": "add, subtract, multiply, or divide",
        "a": "First number",
        "b": "Second number",
    },
)

# Add to main agent
adapter = LangGraphAdapter(
    llm=llm,
    checkpointer=checkpointer,
    additional_tools=[calculator_tool],
)
```

---

## Custom Instructions

```python
adapter = LangGraphAdapter(
    llm=ChatOpenAI(model="gpt-4o"),
    checkpointer=InMemorySaver(),
    custom_section="You are a pirate assistant. Always respond in pirate speak!",
)
```

---

## Available Platform Tools

All LangGraph agents automatically have access to:

| Tool | Description |
|------|-------------|
| `thenvoi_send_message` | Send a message to the chat room |
| `thenvoi_add_participant` | Add a user or agent to the room |
| `thenvoi_remove_participant` | Remove a participant from the room |
| `thenvoi_lookup_peers` | List users/agents that can be added |
| `thenvoi_get_participants` | List current room participants |
| `thenvoi_create_chatroom` | Create a new chat room |
| `thenvoi_send_event` | Send a non-message event such as thought, task, or error |

Contact tools are available when `Capability.CONTACTS` is enabled. Memory tools are available when `Capability.MEMORY` is enabled.

All tools automatically know which room they're operating in through the LangGraph `thread_id`; do not pass room IDs manually.

---

## Running Examples

**From repository:**
```bash
# Simple agent
uv run --extra langgraph python examples/langgraph/01_simple_agent.py

# Agent with custom tools
uv run --extra langgraph python examples/langgraph/02_custom_tools.py

# Agent with custom personality
uv run --extra langgraph python examples/langgraph/03_custom_personality.py

# Calculator sub-graph
uv run --extra langgraph python examples/langgraph/04_calculator_as_tool.py

# RAG sub-graph
uv run --extra langgraph python examples/langgraph/05_rag_as_tool.py

# SQL sub-agent
uv run --extra langgraph python examples/langgraph/06_delegate_to_sql_agent.py

# Multi-agent character demos
uv run --extra langgraph python examples/langgraph/07_tom_agent.py
uv run --extra langgraph python examples/langgraph/08_jerry_agent.py

# Custom operations graph with platform reporting and subgraph delegation
uv run --extra langgraph python examples/langgraph/09_research_ops_orchestrator.py
```

**Using as external library:**
Copy any example to your project and run with:
```bash
uv run python your_agent.py
```

---

## Configuration

All examples use `agent_config.yaml` to store agent credentials:

```yaml
simple_agent:
  agent_id: "agent_123"
  api_key: "key_456"

custom_tools_agent:
  agent_id: "agent_789"
  api_key: "key_012"

custom_personality_agent:
  agent_id: "agent_345"
  api_key: "key_678"

calculator_agent:
  agent_id: "agent_901"
  api_key: "key_234"

rag_agent:
  agent_id: "agent_567"
  api_key: "key_890"

sql_agent:
  agent_id: "agent_246"
  api_key: "key_135"

research_ops_agent:
  agent_id: "agent_ops"
  api_key: "key_ops"

# Also used by multi-agent examples:
tom_agent:
  agent_id: "agent_tom"
  api_key: "key_tom"

jerry_agent:
  agent_id: "agent_jerry"
  api_key: "key_jerry"
```

Load config in your code:

```python
from thenvoi.config import load_agent_config

agent_id, api_key = load_agent_config("simple_agent")
```

---

## Need Help?

- **Start simple:** Try `01_simple_agent.py` first
- **Add tools:** Use `02_custom_tools.py` as a template
- **Sub-agents:** See `04_calculator_as_tool.py` for delegation patterns
- **Main docs:** See [README](../../README.md) for full documentation
