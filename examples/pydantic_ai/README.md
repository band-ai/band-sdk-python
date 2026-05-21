# Pydantic AI Examples for Thenvoi

Examples showing how to use the Thenvoi SDK with Pydantic AI using the composition-based pattern.

## Prerequisites

**Install with Pydantic AI support:**
```bash
uv add "git+https://github.com/thenvoi/thenvoi-sdk-python.git[pydantic_ai]"

# Plus your model provider:
pip install "pydantic-ai-slim[openai]"  # or [anthropic], [google], etc.
```

**Or from repository:**
```bash
uv sync --extra pydantic_ai
```

---

## Quick Start

```python
from thenvoi import Agent
from thenvoi.adapters import PydanticAIAdapter

adapter = PydanticAIAdapter(
    model="openai:gpt-4o",
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
| `01_basic_agent.py` | **Minimal setup** - Simple agent with PydanticAIAdapter. |
| `02_custom_instructions.py` | **Custom behavior** - Agent with custom system prompt. |

---

## Configuration

Create an `agent_config.yaml`:

```yaml
pydantic_agent:
  agent_id: "your-agent-id"
  api_key: "your-api-key"

support_agent:
  agent_id: "your-support-agent-id"
  api_key: "your-support-api-key"
```

Set environment variables:
```bash
export THENVOI_WS_URL="wss://app.band.ai/api/v1/socket/websocket"
export THENVOI_REST_URL="https://app.band.ai"
export OPENAI_API_KEY="your-openai-key"  # for OpenAI models
export ANTHROPIC_API_KEY="your-anthropic-key"  # for Anthropic models
```

---

## Running Examples

```bash
uv run python examples/pydantic_ai/01_basic_agent.py
uv run python examples/pydantic_ai/02_custom_instructions.py
```

---

## Model Strings

Pydantic AI uses model strings in the format `provider:model-name`:

- `openai:gpt-4o`
- `openai:gpt-4o-mini`
- `anthropic:claude-3-5-sonnet-latest`
- `anthropic:claude-3-5-haiku-latest`
- `google:gemini-2.5-pro`

See [Pydantic AI documentation](https://ai.pydantic.dev/) for more model options.

---

## Known Issues

### OpenAI `content: null` Error

When using OpenAI models with complex multi-turn tool usage, you may encounter:

```
Invalid value for 'content': expected a string, got null.
```

This is a [known issue in Pydantic AI](https://github.com/pydantic/pydantic-ai/issues/149) where assistant messages with `tool_calls` but no text content are sent with `content: null`, which OpenAI sometimes rejects.

**Workarounds:**

1. **Use Anthropic instead** (recommended for production):
   ```python
   adapter = PydanticAIAdapter(
       model="anthropic:claude-3-5-sonnet-latest",
       ...
   )
   ```

2. **Use the LangGraph adapter** - handles message history differently:
   ```python
   from thenvoi.adapters import LangGraphAdapter
   ```

3. **Simple conversations work fine** - the issue mainly occurs with complex multi-turn tool sequences.
