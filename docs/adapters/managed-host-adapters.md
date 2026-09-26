# Managed Host Adapter Configuration

Managed hosts keep their own profile data and pass the supported settings to the
adapter at construction. This page is the cross-adapter reference for that
projection; each adapter guide remains the reference for its other settings.

| Adapter | Construction | Custom instructions | Model | Reasoning / thinking |
|---|---|---|---|---|
| Claude SDK | `ClaudeSDKAdapter(...)` | `custom_section` | `model` | `max_thinking_tokens`; `reasoning_effort` is N/A |
| Codex | `CodexAdapter(config=CodexAdapterConfig(...))` | `custom_section` | `model` | `reasoning_effort`; `max_thinking_tokens` is N/A |
| Copilot SDK | `CopilotSDKAdapter(CopilotSDKAdapterConfig(...))` | `custom_section` | `model` | `reasoning_effort`; `max_thinking_tokens` is N/A |
| Copilot CLI (ACP) | `CopilotACPAdapter(CopilotACPAdapterConfig(...))` | `custom_section` | `model` (`--model`) | `reasoning_effort` (`--reasoning-effort`); stdio only, and refused if the same flag is also in `command` |
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

Builds, test suites, and large refactors often need more than these defaults. For OMP and Copilot, `turn_timeout_s` passed as an adapter keyword still works; when the config value is also changed from its default, the config value wins.

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

## Running inside a host: signals and why the agent stopped

`Agent.run()` is the script entry point: it installs SIGTERM/SIGINT handlers while it runs and stops the agent on either signal. `Agent.run_forever()` (and `BandLink.run_forever()`) install no process-wide signal handlers, so a host under launchd, systemd, a desktop app, or a test runner keeps its own and calls `agent.stop()` itself. Hosts that ran the SDK's event loop on a worker thread to keep the transport's handlers inert can drop that.

`run_forever()` also says why it ended:

- it returns normally once the host calls `stop()`, even if the platform disconnects during shutdown;
- it raises `AgentDisconnectedError` on a terminal platform disconnect, such as a second connection with the same agent key superseding this one. `error.reason` is the typed `WebSocketDisconnectReason`, and `agent.last_disconnect_reason` keeps it after `stop()`. Do not restart on it, or two copies fight over one identity.

```python
from band import AgentDisconnectedError, BandConnectionError

assert issubclass(AgentDisconnectedError, BandConnectionError)
```

Compatibility: before this, a supersede made `run_forever()` return as if nothing happened. Scripts that ignored its return now see `AgentDisconnectedError` in that case; that visible failure is intended. Hosts that polled `runtime.link.last_disconnect_reason` beside `run_forever()` to classify the exit can catch the exception instead.

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

## Listing models

Each coding adapter module has an async `list_models(...)` that asks the harness itself, with no model turn, and returns `HarnessModel` entries (`id`, `label`, `provider`, `efforts`, `default_effort`, `is_default`). `id` is the value the adapter's own `model` setting accepts. Temporary processes and clients are closed on every path.

| Module | Call | Source | Tested with |
|---|---|---|---|
| `band.adapters.claude_sdk` | `list_models(adapter)` | `get_server_info()["models"]` | Claude Code 2.1.280 |
| `band.adapters.codex` | `list_models(CodexAdapterConfig(...))` | app-server `model/list`, hidden models left out | Codex 0.156.1 |
| `band.adapters.omp_acp` | `list_models(OmpACPAdapterConfig(...))` | `omp models --json`, falling back to a throwaway ACP session's `model`/`thinking` `configOptions` when that command is unavailable; `id` is the `provider/model` selector | OMP 18.3.2 |
| `band.adapters.copilot_acp` | `list_models(CopilotACPAdapterConfig(...))` | `github-copilot-sdk` `list_models()` (needs the `copilot_sdk` extra) | Copilot CLI 1.0.88, github-copilot-sdk 1.0.14 |

A harness that rejects or lacks the call raises a clear error rather than returning an empty list. Hosts that kept listing code per harness, a Codex `-c model_reasoning_effort` override in `codex_command`, or `--model`/`--reasoning-effort` spliced into Copilot's `command` can use these and the typed settings instead.
