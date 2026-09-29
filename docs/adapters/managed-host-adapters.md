# Managed Host Adapter Configuration

Managed hosts keep their own profile data and pass the supported settings to the
adapter at construction. This page is the cross-adapter reference for that
projection; each adapter guide remains the reference for its other settings.

| Adapter | Construction | Custom instructions | Model | Reasoning / thinking |
|---|---|---|---|---|
| Claude SDK | `ClaudeSDKAdapter(...)` | `custom_section` | `model` | `effort` and `max_thinking_tokens` |
| Codex | `CodexAdapter(config=CodexAdapterConfig(...))` | `custom_section` | `model` | `reasoning_effort`; `max_thinking_tokens` is N/A |
| Copilot SDK | `CopilotSDKAdapter(CopilotSDKAdapterConfig(...))` | `custom_section` | `model` | `reasoning_effort`; `max_thinking_tokens` is N/A |
| OpenCode | `OpencodeAdapter(config=OpencodeAdapterConfig(...))` | `custom_section` | `provider_id` and `model_id` | `variant`; `reasoning_effort` and `max_thinking_tokens` are N/A |

See [OpenCode Integration](opencode.md#opencode-variants) for the `variant` field's semantics and a runnable example.

## Turn timeouts

These adapters take a typed `turn_timeout_s`. When a turn outlives it, the adapter stops the turn and posts a failure with code `timeout` to the room.

| Adapter | Where | Default |
|---|---|---|
| Codex | `CodexAdapterConfig(turn_timeout_s=...)` | `180.0` |
| OMP (ACP) | `OmpACPAdapterConfig(turn_timeout_s=...)` | `300.0` |
| Copilot CLI (ACP) | `CopilotACPAdapterConfig(turn_timeout_s=...)` | `300.0` |
| Cursor (ACP) | `CursorACPAdapterConfig(turn_timeout_s=...)` | `900.0` |

Builds, test suites, and large refactors often need more than these defaults. For OMP and Copilot, `turn_timeout_s` passed as an adapter keyword still works; when the config value is also changed from its default, the config value wins.

```python
from band.adapters.omp_acp import OmpACPAdapter, OmpACPAdapterConfig

config = OmpACPAdapterConfig(turn_timeout_s=1800.0)
adapter = OmpACPAdapter(config)
assert config.turn_timeout_s == 1800.0
```
