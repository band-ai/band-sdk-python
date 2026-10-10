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

## Preflight: fail at start, not on the first turn

`await adapter.preflight()` returns a `PreflightResult` (`ok`, plus `reason` and `remedy` on failure) without touching the platform or running a model turn. Call it before `Agent.start()`, or behind a host's "test" button. It launches a throwaway harness process with the adapter's configured command and environment, completes the handshake, and closes it on every path, cancellation included. No room workspace, session, or thread is created.

| Adapter | Checks | Login |
|---|---|---|
| Claude SDK | spawns `claude` (`cli_path`/`env`), `get_server_info()` | not reported by the handshake; a logged-out CLI fails on its first turn |
| Codex | spawns the app-server (`codex_command`/`codex_env`), `initialize`, then `account/read` | reported: `account` missing with `requiresOpenaiAuth` → "run `codex login`" |
| ACP adapters (OMP, Copilot CLI, Cursor, generic) | spawns the ACP agent, `initialize`, and `authenticate` when `auth_method` is set | not reported by ACP |

Any other `SimpleAdapter` returns `ok` from the default implementation. Hosts that reimplemented each harness handshake to fail fast can drop it.

```python
from band.core.harness import PreflightResult

result = PreflightResult.failed(
    "Codex is not logged in.", "Run `codex login`, then retry."
)
assert (result.ok, result.remedy) == (False, "Run `codex login`, then retry.")
```
