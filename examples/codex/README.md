# Codex Examples for Band

**Bridge** — Codex process owns the model loop; one thread per room. Turn
entry: `handle_turn`. Room posts via Band tools.

## Quick Start

```python notest
from band import Agent, AdapterFeatures, Emit
from band.adapters import CodexAdapter
from band.adapters.codex import CodexAdapterConfig

adapter = CodexAdapter(
    config=CodexAdapterConfig(
        custom_section="You are a helpful assistant. Keep responses concise.",
        approval_policy="never",
    ),
    features=AdapterFeatures(emit={Emit.TASK_EVENTS}),
)

agent = Agent.create(
    adapter=adapter,
    agent_id="your-agent-id",
    api_key="your-api-key",
)
await agent.run()
```

## Examples

| File | Description |
|------|-------------|
| `01_basic_agent.py` | Minimal Codex agent on Band |
| `02_tom_agent.py` / `03_jerry_agent.py` | Multi-agent persona examples |

## Running

```bash
uv run python examples/codex/01_basic_agent.py
```

See [docs/adapters/codex.md](../../docs/adapters/codex.md).
