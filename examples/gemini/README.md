# Gemini SDK Examples for Band

Examples for creating Band agents with the Gemini SDK (**Native** kind).

Band owns the tool loop (`NativeToolLoopBackend` + `GeminiProvider`) behind
`GeminiAdapter`. Agent calls `handle_turn` once per inbound message; room posts
go through tools (`band_send_message`).

## Quick Start

```python notest
from band import Agent
from band.adapters import GeminiAdapter

adapter = GeminiAdapter(
    model="gemini-2.5-flash",
    instructions="You are a helpful assistant.",
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
| `01_basic_agent.py` | Minimal Gemini agent on Band. |

## Running

```bash
uv run python examples/gemini/01_basic_agent.py
```
