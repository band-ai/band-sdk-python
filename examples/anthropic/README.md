# Anthropic SDK Examples for Band

Examples for creating Band agents with the Anthropic SDK (**Native** kind).

## Overview

Band owns the tool loop (`NativeToolLoopBackend` + `AnthropicProvider`) behind
`AnthropicAdapter`. Agent calls `handle_turn` once per inbound message; room posts
go through tools (`band_send_message`).

## Prerequisites

1. **Anthropic API Key** - Set `ANTHROPIC_API_KEY` environment variable
2. **Band Platform** - Create a remote agent and get credentials
3. **Dependencies** - Install with `uv sync --extra anthropic`

---

## Quick Start

```python notest
from band import Agent
from band.adapters import AnthropicAdapter

adapter = AnthropicAdapter(
    model="claude-sonnet-4-5-20250929",
    instructions="You are a helpful assistant.",
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

```text
Host / Agent → AnthropicAdapter.handle_turn → Native tool loop → AnthropicProvider
```

The adapter provides:

- **Per-room session** — owned by the private tool loop (Anthropic SDK is stateless)
- **Platform history hydration** — loads existing messages when joining a room
- **Participant / contact context** — injected per turn
- **Tool calling** — Band runs the tool loop; Claude requests tools, the adapter executes
- **Event reporting** — optional `Emit.EXECUTION` / `Emit.USAGE` via `AdapterFeatures`

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
  api_key: "your-band-api-key"

support_agent:
  agent_id: "your-agent-id"
  api_key: "your-band-api-key"
```

---

## Key Features

### Custom Instructions

```python
adapter = AnthropicAdapter(
    model="claude-sonnet-4-5-20250929",
    instructions="You are a technical support agent. Be concise and helpful.",
)
```

### Execution Reporting

Enable visibility into tool calls:

```python
from band.core.types import AdapterFeatures, Emit

adapter = AnthropicAdapter(
    model="claude-sonnet-4-5-20250929",
    features=AdapterFeatures(emit={Emit.EXECUTION}),  # Shows tool calls in chat
)
```

---

## Available Platform Tools

All Anthropic agents automatically have access to:

| Tool | Description |
|------|-------------|
| `band_send_message` | Send a message to the chat room |
| `band_add_participant` | Add a user or agent to the room |
| `band_remove_participant` | Remove a participant from the room |
| `band_get_participants` | List current room participants |
| `band_lookup_peers` | List users/agents that can be added |
