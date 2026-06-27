# Agno Examples for Band

Examples for building Band agents with the [Agno](https://docs.agno.com)
framework.

## Overview

Agno is model-agnostic: you build and configure your own Agno `Agent` (model,
instructions, tools, database, and other Agno settings), then bridge it to Band with
`AgnoAdapter`. The adapter converts Band room history into Agno messages and runs
your agent, exposing the Band toolset so the agent can reply.

> **Note:** The Band toolset is exposed to the agent — chat and participant
> tools always, plus memory/contact tools when the matching capabilities are
> enabled. The agent must call `band_send_message` to say anything in the room;
> if it only returns plain text, nothing is delivered (the adapter logs that it
> stayed silent). Band guidance is injected into the agent's prompt at startup,
> so a capable model will call the tool on its own — but a minimal agent should
> be instructed to use `band_send_message`. Tool executions are reported to (and
> rehydrated from) the room.

## Prerequisites

1. **A model provider** - Agno is model-agnostic, so you choose the provider and
   set its API key (see [Model providers](#model-providers) below). These
   examples use Anthropic Claude, so set `ANTHROPIC_API_KEY` (or add it to a
   `.env` file).
2. **Band Platform** - Create a remote agent and get credentials, and set
   `BAND_WS_URL` / `BAND_REST_URL` to the platform those credentials belong to
3. **Dependencies** - Install the adapter and your provider (see
   [Installation](#installation) below).

---

## Model providers

Agno is model-agnostic: **you** pick the provider when you build the agent, and
`AgnoAdapter` wraps whatever you pass — nothing in the adapter is tied to a
specific provider. Each provider needs two things: its Python package installed
and its API key in the environment.

| Provider | Import | Package | API key |
|----------|--------|---------|---------|
| Anthropic (examples' default) | `from agno.models.anthropic import Claude` | `anthropic` | `ANTHROPIC_API_KEY` |
| OpenAI | `from agno.models.openai import OpenAIChat` | `openai` | `OPENAI_API_KEY` |
| Google | `from agno.models.google import Gemini` | `google-genai` | `GOOGLE_API_KEY` |
| Groq | `from agno.models.groq import Groq` | `groq` | `GROQ_API_KEY` |

See [Agno's model docs](https://docs.agno.com/models) for the full list.

## Installation

The `agno` extra installs the adapter only — it deliberately does **not** pin a
model provider, so you add the package for the provider you chose above.

**Run an example directly (recommended):** each script declares `band-sdk[agno]`
*and* its provider (`anthropic`) in its PEP 723 metadata, so `uv` installs
everything into an ephemeral environment automatically — no manual setup:

```bash
uv run examples/agno/01_basic_agent.py
```

**Install into your own project/environment:**

```bash
# 1. The adapter (provider-free)
uv sync --extra agno          # or: uv pip install 'band-sdk[agno]'

# 2. Your chosen provider's package
uv pip install anthropic      # or: openai, google-genai, groq, ...
```

---

## Quick Start

```python
# Requires: pip install 'band-sdk[agno]' anthropic   (ANTHROPIC_API_KEY set)
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

Agno is model-agnostic — swap the model and the rest is unchanged. For OpenAI:

```python
# Requires: pip install 'band-sdk[agno]' openai   (OPENAI_API_KEY set)
from agno.models.openai import OpenAIChat

agno_agent = AgnoAgent(
    model=OpenAIChat(id="gpt-4o"),
    instructions="You are a helpful assistant. Be concise and friendly.",
)
adapter = AgnoAdapter(agno_agent)
```

The adapter runs against the agent instance you pass and takes ownership of it:
at startup it configures that instance for Band (replaces its `tools` with a
per-run factory and appends Band guidance to `additional_context`). Don't reuse
the same instance elsewhere.

---

## Examples

| File | Description |
|------|-------------|
| `01_basic_agent.py` | **Minimal setup** - A Claude-backed Agno agent bridged to Band via `AgnoAdapter`. |
| `02_tool_reporting.py` | **Tool-execution reporting** - An Agno agent with its own tools; `AdapterFeatures(emit={Emit.EXECUTION})` posts tool_call/tool_result events to the room. |
| `03_tom_agent.py` | **Character agent (Tom)** - An Agno-backed cat agent. Run alongside Jerry — each is its own Band agent process, so they converse through the room even when backed by different adapters. |
| `04_jerry_agent.py` | **Character agent (Jerry)** - The mouse counterpart to Tom; run the two in separate terminals and add both to a room. |
| `05_memory_secretary.py` | **Band memory tools** - Enables `Capability.MEMORY` so an Agno agent can store and recall durable Band memories. |
| `06_agno_db_history.py` | **Agno-owned history** - Uses `db`, `session_id`, and `add_history_to_context=True`; the adapter disables Band history rehydration to avoid duplicate context. |

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

Provide your model provider's API key via environment variable or a `.env` file
in the repository root. The examples use Anthropic Claude:

```bash
ANTHROPIC_API_KEY=your-anthropic-api-key
```

If you switched the model to another provider (see
[Model providers](#model-providers)), set that provider's key instead — e.g.
`OPENAI_API_KEY`.
