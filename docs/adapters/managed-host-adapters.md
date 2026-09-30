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

## Refusing duplicate instances

Two running copies of one agent id steal each other's room messages and split conversations. Two guards stop a second start. `AgentConfig.single_instance` (on by default) takes a lock in the host's temp dir, so it only sees processes that share one; service managers often give a process its own `TMPDIR` (launchd, systemd `PrivateTmp`), so it misses an interactive start beside a service start. `AgentConfig.conflict_policy` asks the platform, which sees every connection on every host: `SUPERSEDE` (the default) lets the newcomer take over and tells the running agent to stay down, which rolling deploys rely on, and `REJECT` refuses the newcomer instead:

```python
from band import AgentConfig, ConflictPolicy

config = AgentConfig(conflict_policy=ConflictPolicy.REJECT)
assert config.conflict_policy == "reject"
```

Pass `config` to `Agent.create(..., config=config)`. Both guards raise `AgentAlreadyRunningError` (a `BandConfigError`) from `Agent.start()`, so a supervisor can exit without restarting instead of looping:

```python notest
try:
    await agent.start()
except AgentAlreadyRunningError:
    sys.exit(0)  # another instance holds this agent
```

- `REJECT` covers the initial connect only. Automatic reconnects always supersede the agent's own stale socket.
- Across platform pods it is best-effort, and the platform accepts a second connection when its coordination is unreachable. It protects against accidental duplicates; it is not a mutex.
- After `stop()` the platform may need a moment to free the slot, longer after a network drop that never closed the socket, so an immediate restart can occasionally be refused. Supervisors should back off.
- Platforms that predate the setting (before 2026-05) ignore it and keep superseding.
- The adapter starts before the platform connect, so a refused duplicate may start and stop its adapter once.
