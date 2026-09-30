# Managed Host Adapters

What a host that embeds a harness adapter can rely on beyond each adapter guide.

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
