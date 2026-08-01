# Migrating to v0.3.0

v0.3.0 normalized the adapter constructor surface around
`features=AdapterFeatures(...)`. Later releases finished the job: the old
boolean / alias kwargs are **removed** (see
[`MIGRATING-v2.0.md`](MIGRATING-v2.0.md) for the full v2 map). The snippets
below show the **current** spellings.

## TL;DR

```python
from band import AdapterFeatures, Capability, Emit
from band.adapters import AnthropicAdapter

adapter = AnthropicAdapter(
    provider_key="sk-...",
    instructions="Be helpful.",
    features=AdapterFeatures(
        capabilities={Capability.MEMORY},
        emit={Emit.EXECUTION},
    ),
)
assert Capability.MEMORY in adapter.features.capabilities
assert Emit.EXECUTION in adapter.features.emit
```

| Removed (do not use) | Current |
|---|---|
| `enable_memory_tools=True` | `features=AdapterFeatures(capabilities={Capability.MEMORY})` |
| `enable_execution_reporting=True` | `features=AdapterFeatures(emit={Emit.EXECUTION})` |
| `anthropic_api_key=` / `api_key=` (Anthropic) | `provider_key=` |
| `gemini_api_key=` / `api_key=` (Gemini) | `provider_key=` |
| `custom_section=` / `prompt=` (Anthropic, Gemini, …) | `instructions=` |
| `max_tokens=` (Anthropic) | `max_output_tokens=` |

## Universal changes (every adapter)

These apply across the adapter surface:
`AnthropicAdapter`, `GeminiAdapter`, `LangGraphAdapter`, `ClaudeSDKAdapter`,
`CodexAdapter`, `OpencodeAdapter`, `CrewAIAdapter`, `PydanticAIAdapter`,
`GoogleADKAdapter`, `ParlantAdapter`, `LettaAdapter`, `A2AAdapter`,
`A2AGatewayAdapter`, `ACPClientAdapter`.

### Memory tools → `features.capabilities`

```python
from band import AdapterFeatures, Capability

adapter = AnyAdapter(features=AdapterFeatures(capabilities={Capability.MEMORY}))
assert Capability.MEMORY in adapter.kwargs["features"].capabilities
```

### Execution reporting → `features.emit`

```python
from band import AdapterFeatures, Emit

adapter = AnyAdapter(features=AdapterFeatures(emit={Emit.EXECUTION}))
assert Emit.EXECUTION in adapter.kwargs["features"].emit
```

### Codex thoughts and task events

Config booleans (`enable_execution_reporting`, `emit_thought_events`,
`enable_task_events`) were removed. Pass emit channels on `features=`.
When `features` is omitted, Codex still defaults to `{Emit.TASK_EVENTS}`.

```python
from band import AdapterFeatures, Emit
from band.adapters import CodexAdapter, CodexAdapterConfig

adapter = CodexAdapter(
    config=CodexAdapterConfig(),
    features=AdapterFeatures(
        emit={Emit.EXECUTION, Emit.THOUGHTS, Emit.TASK_EVENTS},
    ),
)
assert adapter.features.emit == frozenset(
    {Emit.EXECUTION, Emit.THOUGHTS, Emit.TASK_EVENTS}
)

defaulted = CodexAdapter()
assert Emit.TASK_EVENTS in defaulted.features.emit
```

### `ClaudeSDKAdapter` execution + thoughts

ClaudeSDK used to fold thought emission into `enable_execution_reporting`.
Request both channels explicitly:

```python
from band import AdapterFeatures, Emit
from band.adapters import ClaudeSDKAdapter

adapter = ClaudeSDKAdapter(
    features=AdapterFeatures(emit={Emit.EXECUTION, Emit.THOUGHTS}),
)
assert Emit.EXECUTION in adapter.features.emit
assert Emit.THOUGHTS in adapter.features.emit
```

Execution reporting **without** thought events:

```python
from band import AdapterFeatures, Emit
from band.adapters import ClaudeSDKAdapter

adapter = ClaudeSDKAdapter(features=AdapterFeatures(emit={Emit.EXECUTION}))
assert adapter.features.emit == frozenset({Emit.EXECUTION})
```

### Removed kwargs raise `TypeError`

Legacy boolean / alias kwargs are rejected (they must not silently land in
`**provider_options`):

```python
from band.adapters import AnthropicAdapter

try:
    AnthropicAdapter(enable_memory_tools=True)
except TypeError as exc:
    assert "features" in str(exc)
else:
    raise AssertionError("expected TypeError")
```

## Selective renames (`AnthropicAdapter` and `GeminiAdapter`)

### API keys → `provider_key`

```python
from band.adapters import AnthropicAdapter, GeminiAdapter

anthropic = AnthropicAdapter(provider_key="sk-...")
gemini = GeminiAdapter(provider_key="AIza-...")
assert anthropic.model
assert gemini.model
```

### Instructions

Phase-1 adapters take `instructions=` (bare `str` appends; use
`Instruction(..., mode=InstructionMode.REPLACE)` to replace the whole
prompt). LangGraph / Codex / CrewAI / etc. still use their native prompt
fields (`custom_section`, `backstory`, …).

```python
from band.adapters import AnthropicAdapter

adapter = AnthropicAdapter(instructions="Be helpful.")
assert adapter._instructions is not None
```

### `include_base_instructions` (Anthropic + Gemini)

Opt out of the SDK's built-in base instructions while keeping the agent
identity header:

```python
from band.adapters import AnthropicAdapter

adapter = AnthropicAdapter(
    instructions="You are a totally custom bot.",
    include_base_instructions=False,
)
assert adapter._include_base_instructions is False
```

## Capability-gated prompt sections

`InstructionPolicy` / system-prompt rendering includes memory and contact
tool instructions only when the corresponding `Capability` is set:

```python
from band import AdapterFeatures, Capability
from band.adapters import AnthropicAdapter

adapter = AnthropicAdapter(
    features=AdapterFeatures(capabilities={Capability.MEMORY}),
)
assert Capability.MEMORY in adapter.features.capabilities
```

If your adapter sets `Capability.CONTACTS`, the rendered prompt also
contains a contact-management tools section.

## Hub-room auto-enables contact tools

When `ContactEventStrategy.HUB_ROOM` is active, the runtime
automatically exposes contact-management tool schemas to the LLM in the
hub room for adapters that source schemas from `AgentTools.get_tool_schemas()`.

Adapters that register tool functions manually (for example CrewAI and
PydanticAI) still gate contact tools with `Capability.CONTACTS`, so keep
that capability enabled for hub-room contact management on those adapters.

## Exception hierarchy

v0.3.0 adds four exception classes at the package root:

```python
from band import (
    BandError,  # Base for all SDK exceptions
    BandConfigError,  # Configuration / setup errors
    BandConnectionError,  # Transport (WebSocket / REST) failures
    BandToolError,  # Tool execution failures
)

assert issubclass(BandConfigError, BandError)
assert issubclass(BandConnectionError, BandError)
assert issubclass(BandToolError, BandError)
```

`AgentTools.send_message()` raises `BandToolError` when called with no
resolvable mentions, instead of returning a `{"error": "..."}` dict. The
dispatch path through `execute_tool_call()` still surfaces the error as a
string for the LLM, so adapters using `execute_tool_call()` need no
changes.

`BandConfigError` ships with a `with_suggestion()` factory that attaches
"Did you mean 'X'?" hints based on Levenshtein distance:

```python
from band import BandConfigError, Capability

err = BandConfigError.with_suggestion(
    "Unknown capability 'memry'.",
    "memry",
    [c.value for c in Capability],
)
assert isinstance(err, BandConfigError)
assert "memory" in str(err)
```

## `Agent.from_config()`

A convenience factory loads credentials from a YAML config file:

```python fixture:agent_config_path
from band import Agent
from band.adapters import AnthropicAdapter

agent = Agent.from_config(
    "researcher",
    adapter=AnthropicAdapter(),
)
await agent.run()
```

The adapter is still constructed in Python — only the credentials come
from YAML. This preserves type safety for adapter-specific options.

## Optional dependency: `claude-sdk`

`claude-agent-sdk` moved from a hard dependency to the `claude-sdk`
optional extra. If you were using `ClaudeSDKAdapter`, install the extra:

```bash
pip install band-sdk[claude-sdk]
# or
uv add band-sdk[claude-sdk]
```

If you do not use `ClaudeSDKAdapter`, you no longer pull in
`claude-agent-sdk` (and its Node.js requirement).

## Current status

The constructor aliases listed in the TL;DR table are **removed** in v2.0.
Use the current spellings above; see [`MIGRATING-v2.0.md`](MIGRATING-v2.0.md)
for the complete redesign map (instructions, providers, gateways).
