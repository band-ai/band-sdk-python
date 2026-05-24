# Anthropic Adapter

The [Anthropic SDK](https://docs.anthropic.com/) provides direct access to Claude models via the Anthropic API. This adapter wraps the async Anthropic client, manages per-room conversation history, and handles tool execution loops.

## Install

```bash
uv add "thenvoi-sdk[anthropic]"
```

## How It Works

The adapter converts Thenvoi room history into Anthropic message format (alternating user/assistant turns), renders a system prompt from the SDK's collaboration template plus your custom instructions, and runs the Claude model with Thenvoi platform tools converted to Anthropic tool schemas. Each room maintains its own conversation history. Tool calls are executed in a loop until the model produces a final text response.

## Quick Start

```python
from thenvoi.adapters import AnthropicAdapter

adapter = AnthropicAdapter(model="claude-sonnet-4-5-20250929")
```

## Configuration Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | `str` | `"claude-sonnet-4-5-20250929"` | Anthropic model ID. |
| `api_key` | `str \| None` | `None` | Anthropic API key. Falls back to `ANTHROPIC_API_KEY` env var. |
| `prompt` | `str \| None` | `None` | Custom instructions appended to the SDK's base prompt. |
| `system_prompt` | `str \| None` | `None` | Replaces the SDK's default system prompt entirely. When set, `prompt` and `include_base_instructions` are bypassed. |
| `include_base_instructions` | `bool` | `True` | Include the SDK's base collaboration instructions. Only relevant when `system_prompt` is not set. |
| `max_tokens` | `int` | `4096` | Max tokens per response. |
| `additional_tools` | `list[CustomToolDef]` | `None` | Custom tools as `(PydanticModel, callable)` tuples. |
| `features` | `AdapterFeatures` | `None` | Capabilities and emit options. |
| `history_converter` | `AnthropicHistoryConverter` | auto | Override the default history converter. |

> **Note on `prompt` vs `custom_section`:** This adapter uses `prompt` (not `custom_section`) for custom instructions. The older `custom_section` parameter is deprecated and will be removed.

## Capabilities and Emit

| Feature | Supported |
|---------|-----------|
| `Capability.CONTACTS` | Yes |
| `Capability.MEMORY` | Yes |
| `Emit.EXECUTION` | Yes |
| `Emit.THOUGHTS` | - |
| `Emit.TASK_EVENTS` | - |

When `Emit.EXECUTION` is enabled, the adapter sends `tool_call` and `tool_result` events with JSON payloads containing the tool name, args/output, and a `tool_call_id` for linking calls to results.

```python
from thenvoi import AdapterFeatures, Capability, Emit
from thenvoi.adapters import AnthropicAdapter

adapter = AnthropicAdapter(
    model="claude-sonnet-4-5-20250929",
    features=AdapterFeatures(
        capabilities={Capability.CONTACTS},
        emit={Emit.EXECUTION},
    ),
)
```

## Custom Tools

Uses the Thenvoi custom-tool tuple format — a Pydantic input model plus a callable:

```python
from pydantic import BaseModel, Field

class WeatherInput(BaseModel):
    """Get current weather for a city."""
    city: str = Field(description="City name")

def get_weather(args: WeatherInput) -> str:
    return f"Sunny, 22°C in {args.city}"

adapter = AnthropicAdapter(
    model="claude-sonnet-4-5-20250929",
    additional_tools=[(WeatherInput, get_weather)],
)
```

## Examples

See [examples/anthropic/](../../examples/anthropic/) for runnable scripts.

| File | Description |
|------|-------------|
| `01_basic_agent.py` | Minimal Anthropic agent |
| `02_custom_instructions.py` | Custom prompt instructions |
| `03_tom_agent.py` | Tom agent for multi-agent demo |
| `04_jerry_agent.py` | Jerry agent for multi-agent demo |
| `05_contact_management.py` | Contact event handling |
