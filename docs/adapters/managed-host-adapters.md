# Managed Host Adapter Configuration

Managed hosts keep their own profile data and pass the supported settings to the
adapter at construction. This page is the cross-adapter reference for that
projection; each adapter guide remains the reference for its other settings.

| Adapter | Construction | Custom instructions | Model | Reasoning / thinking |
|---|---|---|---|---|
| Claude SDK | `ClaudeSDKAdapter(...)` | `custom_section` | `model` | `max_thinking_tokens`; `reasoning_effort` is N/A |
| Codex | `CodexAdapter(config=CodexAdapterConfig(...))` | `custom_section` | `model` | `reasoning_effort`; `max_thinking_tokens` is N/A |
| Copilot SDK | `CopilotSDKAdapter(CopilotSDKAdapterConfig(...))` | `custom_section` | `model` | `reasoning_effort`; `max_thinking_tokens` is N/A |
| OpenCode | `OpencodeAdapter(config=OpencodeAdapterConfig(...))` | `custom_section` | `provider_id` and `model_id` | `variant`; `reasoning_effort` and `max_thinking_tokens` are N/A |

See [OpenCode Integration](opencode.md#opencode-variants) for the `variant` field's semantics and a runnable example.

## Turn timeouts

Every coding adapter takes a typed `turn_timeout_s`. When a turn outlives it, the adapter stops the turn and posts a failure with code `timeout` to the room.

| Adapter | Where | Default |
|---|---|---|
| Claude SDK | `ClaudeSDKAdapter(turn_timeout_s=...)` | `None` (unbounded) |
| Codex | `CodexAdapterConfig(turn_timeout_s=...)` | `180.0` |
| OMP (ACP) | `OmpACPAdapterConfig(turn_timeout_s=...)` | `300.0` |
| Copilot CLI (ACP) | `CopilotACPAdapterConfig(turn_timeout_s=...)` | `300.0` |
| Cursor (ACP) | `CursorACPAdapterConfig(turn_timeout_s=...)` | `900.0` |

Builds, test suites, and large refactors often need more than these defaults. For OMP and Copilot, `turn_timeout_s` passed as an adapter keyword still works; setting it there and to a different non-default value on the config raises `ValueError`.

```python
from band.adapters.omp_acp import OmpACPAdapter, OmpACPAdapterConfig

config = OmpACPAdapterConfig(turn_timeout_s=1800.0)
adapter = OmpACPAdapter(config)
assert config.turn_timeout_s == 1800.0
```

## Single-instance lock directory

The agent runtime refuses to start when another process on the host already runs the same agent id. The lock file lives in the process temp dir by default, and processes only contend when they share that directory. Service managers often give a process its own `TMPDIR` (launchd, systemd `PrivateTmp`), so an interactive start and a service start of one agent would miss each other. Pin the directory with `AgentConfig.single_instance_lock_dir`:

```python
from band.runtime.types import AgentConfig

config = AgentConfig(single_instance_lock_dir="/var/run/band-agents")
assert config.single_instance
```

Pass `config` to `Agent.create(..., config=config)`. Hosts that kept a second lock file beside the SDK guard can drop it.
