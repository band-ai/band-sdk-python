# Migrating to v0.3.0

v0.3.0 normalizes the adapter constructor surface across every framework
adapter. Existing code keeps working — old parameters emit a
`DeprecationWarning` for one release and will be removed in v0.4.0.

This guide shows what changed and how to update your code.

## TL;DR

```python
# Before (v0.2.x)
adapter = AnthropicAdapter(
    anthropic_api_key="sk-...",
    custom_section="Be helpful.",
    enable_memory_tools=True,
    enable_execution_reporting=True,
)

# After (v0.3.0)
from thenvoi import AdapterFeatures, Capability, Emit

adapter = AnthropicAdapter(
    api_key="sk-...",
    prompt="Be helpful.",
    features=AdapterFeatures(
        capabilities={Capability.MEMORY},
        emit={Emit.EXECUTION},
    ),
)
```

## Universal changes (every adapter)

These apply to all 14 adapters:
`AnthropicAdapter`, `GeminiAdapter`, `LangGraphAdapter`, `ClaudeSDKAdapter`,
`CodexAdapter`, `OpencodeAdapter`, `CrewAIAdapter`, `PydanticAIAdapter`,
`GoogleADKAdapter`, `ParlantAdapter`, `LettaAdapter`, `A2AAdapter`,
`A2AGatewayAdapter`, `ACPClientAdapter`.

### `enable_memory_tools` → `features.capabilities`

```python
# Before
adapter = AnyAdapter(enable_memory_tools=True)

# After
adapter = AnyAdapter(features=AdapterFeatures(capabilities={Capability.MEMORY}))
```

### `enable_execution_reporting` → `features.emit`

```python
# Before
adapter = AnyAdapter(enable_execution_reporting=True)

# After
adapter = AnyAdapter(features=AdapterFeatures(emit={Emit.EXECUTION}))
```

### Codex `emit_thought_events` and `enable_task_events`

```python
# Before
config = CodexAdapterConfig(
    enable_execution_reporting=True,
    emit_thought_events=True,
    enable_task_events=True,
)
adapter = CodexAdapter(config=config)

# After
adapter = CodexAdapter(
    config=CodexAdapterConfig(...),  # framework-specific options remain on the config
    features=AdapterFeatures(
        emit={Emit.EXECUTION, Emit.THOUGHTS, Emit.TASK_EVENTS},
    ),
)
```

The config-based adapters (`CodexAdapter`, `OpencodeAdapter`, `LettaAdapter`)
still build features automatically from the config booleans if you do not
pass `features=` explicitly, so existing config-only callers keep working.

### `ClaudeSDKAdapter` execution + thoughts

`ClaudeSDKAdapter` historically emitted thought events under the
`enable_execution_reporting` flag. The migration preserves this:

```python
# Before
adapter = ClaudeSDKAdapter(enable_execution_reporting=True)

# After (equivalent)
adapter = ClaudeSDKAdapter(
    features=AdapterFeatures(emit={Emit.EXECUTION, Emit.THOUGHTS}),
)
```

If you want execution reporting **without** thought events, use:

```python
adapter = ClaudeSDKAdapter(features=AdapterFeatures(emit={Emit.EXECUTION}))
```

### Conflict detection

Passing both old booleans and the new `features=` raises
`ThenvoiConfigError`:

```python
# Raises ThenvoiConfigError
adapter = AnthropicAdapter(
    enable_memory_tools=True,
    features=AdapterFeatures(capabilities={Capability.MEMORY}),
)
```

## Selective renames (`AnthropicAdapter` and `GeminiAdapter` only)

### `anthropic_api_key` / `gemini_api_key` → `api_key`

```python
# Before
adapter = AnthropicAdapter(anthropic_api_key="sk-...")
adapter = GeminiAdapter(gemini_api_key="AIza-...")

# After
adapter = AnthropicAdapter(api_key="sk-...")
adapter = GeminiAdapter(api_key="AIza-...")
```

### `custom_section` → `prompt`

```python
# Before
adapter = AnthropicAdapter(custom_section="Be helpful.")

# After
adapter = AnthropicAdapter(prompt="Be helpful.")
```

The other adapters keep `custom_section` because their natural prompt
concept differs (e.g. CrewAI uses `backstory`, LangGraph uses
`prompt_template`).

### New: `include_base_instructions` (Anthropic + Gemini)

You can now opt out of the SDK's built-in base instructions while keeping
the agent identity header:

```python
adapter = AnthropicAdapter(
    prompt="You are a totally custom bot.",
    include_base_instructions=False,
)
```

## Capability-gated prompt sections

`render_system_prompt()` now includes memory and contact tool
instructions only when the corresponding `Capability` is set:

```python
adapter = AnthropicAdapter(
    features=AdapterFeatures(capabilities={Capability.MEMORY}),
)
# adapter._system_prompt now contains a "## Memory Tools" section
```

If your adapter sets `Capability.CONTACTS`, the rendered prompt also
contains a "## Contact Management Tools" section.

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
from thenvoi import (
    ThenvoiError,           # Base for all SDK exceptions
    ThenvoiConfigError,     # Configuration / setup errors
    ThenvoiConnectionError, # Transport (WebSocket / REST) failures
    ThenvoiToolError,       # Tool execution failures
)
```

`AgentTools.send_message()` now raises `ThenvoiToolError` when called
with no resolvable mentions, instead of returning a `{"error": "..."}`
dict. The dispatch path through `execute_tool_call()` still surfaces the
error as a string for the LLM, so adapters using `execute_tool_call()`
need no changes.

`ThenvoiConfigError` ships with a `with_suggestion()` factory that
attaches "Did you mean 'X'?" hints based on Levenshtein distance:

```python
raise ThenvoiConfigError.with_suggestion(
    "Unknown capability 'memry'.",
    "memry",
    [c.value for c in Capability],
)
# ThenvoiConfigError: Unknown capability 'memry'. Did you mean 'memory'?
```

## `Agent.from_config()`

A new convenience factory loads credentials from a YAML config file:

```python
from thenvoi import Agent

agent = Agent.from_config(
    "researcher",
    adapter=AnthropicAdapter(...),
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

## Removal timeline

| Item | Deprecated in | Removed in |
|------|---------------|-----------|
| `enable_memory_tools` (all adapters) | v0.3.0 | v0.4.0 |
| `enable_execution_reporting` (all adapters) | v0.3.0 | v0.4.0 |
| `anthropic_api_key` (Anthropic) | v0.3.0 | v0.4.0 |
| `gemini_api_key` (Gemini) | v0.3.0 | v0.4.0 |
| `custom_section` (Anthropic, Gemini) | v0.3.0 | v0.4.0 |

All shims emit a `DeprecationWarning` so you can find every callsite
with `python -W error::DeprecationWarning` or
`pytest -W error::DeprecationWarning`.
