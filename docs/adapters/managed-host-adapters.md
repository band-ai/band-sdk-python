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

## Single-instance lock directory

The agent runtime refuses to start when another process on the host already runs the same agent id. The lock file lives in the process temp dir by default, and processes only contend when they share that directory. Service managers often give a process its own `TMPDIR` (launchd, systemd `PrivateTmp`), so an interactive start and a service start of one agent would miss each other. Pin the directory with `AgentConfig.single_instance_lock_dir`:

```python
from band.runtime.types import AgentConfig

config = AgentConfig(single_instance_lock_dir="/var/run/band-agents")
assert config.single_instance
```

Pass `config` to `Agent.create(..., config=config)`. Hosts that kept a second lock file beside the SDK guard can drop it.
