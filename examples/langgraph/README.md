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
- Set `OPENAI_API_KEY` environment variable
- Configure agent credentials (see main [README](../../README.md#creating-external-agents-on-thenvoi-platform))

---

## Quick Start

```python
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

# Create adapter with LLM and checkpointer
adapter = LangGraphAdapter(
    llm=ChatOpenAI(model="gpt-5.4-mini"),
    checkpointer=InMemorySaver(),
)

# Create and run agent
agent = Agent.create(
    adapter=adapter,
    agent_id="your-agent-id",
    api_key="your-api-key",
    ws_url="wss://app.thenvoi.com/api/v1/socket/websocket",
    rest_url="https://app.thenvoi.com",
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
| `03_custom_personality.py` | **Custom personality** - Built-in agent + pirate personality using `custom_instructions`. |

### Advanced: Delegating to Sub-Agents

| File | Description |
|------|-------------|
| `04_calculator_as_tool.py` | **Calculator sub-graph** - Delegates math to calculator sub-graph using `graph_as_tool()`. |
| `05_rag_as_tool.py` | **RAG sub-graph** - Delegates research to RAG sub-graph with vector search. |
| `06_delegate_to_sql_agent.py` | **SQL sub-agent** - Delegates database queries to SQL expert. |

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
    llm=ChatOpenAI(model="gpt-5.4-mini"),
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
    description="Evaluates math expressions"
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
    llm=ChatOpenAI(model="gpt-5.4-mini"),
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
| `thenvoi_get_participants` | List current room participants |
| `thenvoi_lookup_peers` | List users/agents that can be added |

**Note:** All tools automatically know which room they're operating in (via `thread_id`). No need to pass room IDs manually.

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
