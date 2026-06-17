# Agno Examples for Band

Examples for building Band agents with the [Agno](https://docs.agno.com)
framework.

## Overview

Agno is model-agnostic: you build and configure your own Agno `Agent` (model,
instructions, and — in a later iteration — tools), then bridge it to Band with
`AgnoAdapter`. The adapter converts Band room history into Agno messages, runs
your agent, and replies with its text output.

> **Note:** The Band toolset is exposed to the agent — chat and participant
> tools always, plus memory/contact tools when the matching capabilities are
> enabled. If the agent doesn't post via `band_send_message`, the adapter sends
> its final text as a fallback, so simple agents reply without extra prompting.
> Tool executions are reported to (and rehydrated from) the room.

## Prerequisites

1. **Anthropic API Key** - Set `ANTHROPIC_API_KEY` (or add it to a `.env` file)
2. **Band Platform** - Create a remote agent and get credentials, and set
   `BAND_WS_URL` / `BAND_REST_URL` to the platform those credentials belong to
3. **Dependencies** - Install with `uv sync --extra agno`

---

## Quick Start

```python
from agno.agent import Agent as AgnoAgent
from agno.models.anthropic import Claude

from band import Agent
from band.adapters import AgnoAdapter

# You own the Agno agent — model, instructions, tools.
agno_agent = AgnoAgent(
    model=Claude(id="claude-sonnet-4-6"),
    instructions="You are a helpful assistant. Be concise and friendly.",
)

# Bridge it to Band.
adapter = AgnoAdapter(agno_agent)
agent = Agent.from_config("agno_agent", adapter=adapter)
await agent.run()
```

---

## Examples

| File | Description |
|------|-------------|
| `01_basic_agent.py` | **Minimal setup** - A Claude-backed Agno agent bridged to Band via `AgnoAdapter`. |
| `02_tool_reporting.py` | **Tool-execution reporting** - An Agno agent with its own tools; `AdapterFeatures(emit={Emit.EXECUTION})` posts tool_call/tool_result events to the room. |
| `03_tom_and_jerry.py` | **Two agents in one process** - Tom and Jerry, each its own Agno-backed Band agent with a distinct personality, run concurrently with `asyncio.gather`. |

---

## Running Examples

```bash
# From repository root
cp agent_config.yaml.example agent_config.yaml
# edit the agno_agent entry in agent_config.yaml with your Band agent_id + api_key

uv run examples/agno/01_basic_agent.py
```

`Agent.from_config` looks for `agent_config.yaml` in the current working
directory, so run from the directory that contains it.

---

## Configuration

Add your agent credentials to `agent_config.yaml`:

```yaml
agno_agent:
  agent_id: "your-agent-id"
  api_key: "your-band-api-key"
```

Provide your Anthropic API key via environment variable or a `.env` file in the
repository root:

```bash
ANTHROPIC_API_KEY=your-anthropic-api-key
```
