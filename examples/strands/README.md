# Strands Agents Examples for Band

Examples for running [AWS Strands Agents](https://strandsagents.com) on the Band
platform through `StrandsAdapter`.

## Prerequisites

1. **Dependencies** — `uv sync --extra strands` (or `pip install band-sdk[strands]`)
2. **Band platform** — a registered agent plus `BAND_WS_URL` / `BAND_REST_URL`
3. **Model credentials** — for these examples `OPENAI_API_KEY`, except
   `05_bedrock_model.py`, which needs AWS credentials with Bedrock access
4. **`agent_config.yaml`** — copy `agent_config.yaml.example` from the repo root and
   fill in `strands_agent` (and `tom_agent` / `jerry_agent` for the character pair)

## Picking a model

Strands has **no provider-prefix string shorthand**: a bare string means a Bedrock
model id. Any other provider is constructed explicitly.

```python notest
from strands.models import BedrockModel
from strands.models.openai import OpenAIModel

from band.adapters import StrandsAdapter

StrandsAdapter(model=OpenAIModel(model_id="gpt-5.4-mini"))          # OpenAI
StrandsAdapter(model="us.anthropic.claude-sonnet-4-5-20250929-v1:0")  # Bedrock id
StrandsAdapter(model=BedrockModel(model_id="...", region_name="us-east-1"))
```

Providers beyond `openai` need their own Strands extra (e.g.
`strands-agents[anthropic]`).

## Examples

| File | Shows |
| ---- | ----- |
| `01_basic_agent.py` | Minimal agent: a model plus `custom_section` |
| `02_custom_tools.py` | Portable `(InputModel, handler)` tools, memory + contact tools, execution/usage events |
| `03_custom_instructions.py` | `system_prompt` full override and the messaging contract it must carry |
| `04_native_tools.py` | Strands' own `@tool` functions, and `band_terminal` for a tool that ends the turn |
| `05_bedrock_model.py` | Amazon Bedrock, via the bare model id and via `BedrockModel` |
| `06_tom_agent.py` / `07_jerry_agent.py` | Two Strands peers talking to each other in one room |

## Prompt, tools, capabilities

```python notest
from band.core.types import AdapterFeatures, Capability, Emit

StrandsAdapter(
    model=model,
    custom_section="Appended to the SDK-rendered Band prompt (recommended)",
    system_prompt="Replaces it entirely — you own the tool contract then",
    additional_tools=[(WeatherInput, get_weather), native_strands_tool],
    features=AdapterFeatures(
        emit=frozenset({Emit.EXECUTION, Emit.USAGE}),
        capabilities=frozenset({Capability.MEMORY, Capability.CONTACTS}),
    ),
)
```

The adapter registers the Band platform tools itself; a custom tool may not reuse
one of their names. Band owns per-room history: the transcript is converted into
Strands `Message` dicts, seeded into a fresh `Agent` per turn, and read back after
it, so a restart rehydrates from the room.

## Running

```bash
# from the repo root
uv run examples/strands/01_basic_agent.py

# the character pair, in two terminals
uv run examples/strands/06_tom_agent.py
uv run examples/strands/07_jerry_agent.py
```
