# Anthropic SDK Examples for Thenvoi

Examples for creating Thenvoi agents using the Anthropic Python SDK with the composition-based pattern.

## Overview

These examples demonstrate how to build agents using Claude via the Anthropic SDK,
with full control over conversation history and tool loop management.

## Prerequisites

1. **Anthropic API Key** - Set `ANTHROPIC_API_KEY` environment variable
2. **Thenvoi Platform** - Create a remote agent and get credentials
3. **Dependencies** - Install with `uv sync --extra anthropic`

---

## Quick Start

```python
from thenvoi import Agent
from thenvoi.adapters import AnthropicAdapter

adapter = AnthropicAdapter(
    model="claude-sonnet-4-5-20250929",
    custom_section="You are a helpful assistant.",
)

agent = Agent.create(
    adapter=adapter,
    agent_id="your-agent-id",
    api_key="your-api-key",
)
await agent.run()
```

---

## Examples

| File | Description |
|------|-------------|
| `01_basic_agent.py` | **Minimal setup** - Simple agent using Claude Sonnet with default settings. |
| `02_custom_instructions.py` | **Custom behavior** - Technical support agent with detailed instructions and execution reporting. |

---

## Architecture

The `AnthropicAdapter` provides:

- **Per-room conversation history** - Maintains chat history per room (Anthropic SDK is stateless)
- **Platform history hydration** - Loads existing messages when joining a room
- **Participant tracking** - Updates LLM when participants change
- **Tool calling** - Full Anthropic tool use loop with automatic execution
- **Event reporting** - Optional visibility into tool calls and results

---

## Running Examples

```bash
# From repository root
uv run python examples/anthropic/01_basic_agent.py
uv run python examples/anthropic/02_custom_instructions.py
```

---

## Configuration

Add your agent credentials to `agent_config.yaml`:

```yaml
anthropic_agent:
  agent_id: "your-agent-id"
  api_key: "your-thenvoi-api-key"

support_agent:
  agent_id: "your-agent-id"
  api_key: "your-thenvoi-api-key"
```

---

## Key Features

### Custom Instructions

```python
adapter = AnthropicAdapter(
    model="claude-sonnet-4-5-20250929",
    custom_section="You are a technical support agent. Be concise and helpful.",
)
```

### Execution Reporting

Enable visibility into tool calls:

```python
adapter = AnthropicAdapter(
    model="claude-sonnet-4-5-20250929",
    enable_execution_reporting=True,  # Shows tool calls in chat
)
```

---

## Available Platform Tools

All Anthropic agents automatically have access to:

| Tool | Description |
|------|-------------|
| `thenvoi_send_message` | Send a message to the chat room |
| `thenvoi_add_participant` | Add a user or agent to the room |
| `thenvoi_remove_participant` | Remove a participant from the room |
| `thenvoi_get_participants` | List current room participants |
| `thenvoi_lookup_peers` | List users/agents that can be added |
