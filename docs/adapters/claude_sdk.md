# Claude Agent SDK Adapter

`ClaudeSDKAdapter` runs Claude Code as a subprocess, one session per Band room.
Runnable scripts: [examples/claude_sdk/](../../examples/claude_sdk/).

Automatic idle release is unavailable pending native safety qualification;
see [idle room resource retention](../idle-room-release.md).

Like the other adapters, it takes one settings object,
`ClaudeSDKAdapterConfig`, plus the keyword-only `additional_tools`,
`history_converter` and feature flags (`emit=`, `capabilities=`, ...). The
config is a frozen pydantic model that refuses unknown fields, so a misspelt
setting fails when the adapter is built rather than being silently ignored.
CLI launch options and chat approvals are nested groups:
`cli=ClaudeCLIOptions(...)` and `approvals=ClaudeApprovalOptions(...)`.

By default, every room runs in its own `.band-workspaces/<room-id>` directory
under the host's working directory. Pass `workspace_for_room=` as a keyword-only
constructor argument to choose another absolute directory for each room; use
`band.create_room_workspace_resolver(root)` for isolated children of a custom root.
The resolver is evaluated once per room membership and the directory stays stable
through session recovery. Leaving or stopping releases the claim without deleting
files. Workspace claims are local to one adapter instance; they are not an OS
sandbox or a lock across processes.

An explicit `ClaudeSDKAdapterConfig(cwd=...)` preserves the shared-directory
behavior for callers who need it. It cannot be combined with `workspace_for_room`.
Failed session cleanup retains ownership and can be retried; a new session cannot
reuse the failed client or take another room's claimed directory.

## Room ownership

Each room's session is owned through that room's residency
(`band.core.retention`): its connect, close and recovery run independently,
so a slow CLI in one room never holds up another.

- **A turn owns the room until it settles.** The message's turn is enrolled
  before it starts and keeps the room leased through its query, tool calls,
  approval waits and outcome, including after `on_message` returns early for a
  manual approval. Leaving the room, or `cleanup_all`, closes admission at
  once, cancels and joins that turn, and only then closes the CLI.
- **A CLI is held until its close succeeds.** A failed or cancelled startup, a
  crashed CLI, a turn that stopped before its result and a replaced Band MCP
  server all retire the CLI through the room's owner once the turn unwinds.
  The next message waits for that close, then resumes the session in the same
  workspace; a crashed CLI starts a new one. A permission request from a
  retired CLI is denied without reaching the room.
- **One reader spans turns.** CLI output that arrives while no turn is
  listening, such as background work finishing after a result, is logged and
  dropped instead of being read by the next turn. Output read after the next
  turn starts listening is attributed to that turn, because the CLI tags its
  messages with neither the turn nor the work that produced them.
- **Shutdown shares one budget.** `cleanup_all` closes every room, then the
  shared Band MCP server, within one `retention.transition_timeout_s`. An
  unfinished or failed step raises, stays owned, and a retry joins it; the
  server stays up while any room's CLI may still dial it.

```python
import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapterConfig
from band.core.retention import RoomRetentionConfig

config = ClaudeSDKAdapterConfig(retention=RoomRetentionConfig(transition_timeout_s=30))
assert config.retention.idle_timeout_s is None

with pytest.raises(ValueError, match="idle retention is unavailable"):
    ClaudeSDKAdapterConfig(retention=RoomRetentionConfig(idle_timeout_s=60))
```

- **Two credentials.** `Agent.create(api_key=...)` is the Band key only. Claude
  Code authenticates itself (`claude auth login` or `ANTHROPIC_API_KEY`); the
  adapter never hands it a key.
- **Band tools are bound to the room.** The adapter hosts one loopback
  `LocalMCPServer` and gives each room's session that room's endpoint
  (`/rooms/<room>/mcp`), so the tools take no `chat_id` and the prompt never
  states one. If that server dies, the next message replaces it on a new port
  and each room's session resumes against it on that room's next message; a
  turn already in flight loses its Band tools until it ends.
- **Assistant text is never posted.** The adapter only debug-logs it. A reply
  reaches the room through the `band_send_message` tool, and a turn that ends
  with no successful reply or action tool call is reported to the room as an
  error.
- **`approvals` gates everything except Band's own tools.** With it set, every
  tool call that is not `mcp__band__*` or `ToolSearch` goes through the
  adapter's approval callback (`mode="manual"` asks the room). Band's tools are
  never gated, in any mode.
- **Host Claude Code config is ignored.** `setting_sources` defaults to `()`, so
  skills, subagents and settings under `~/.claude` and `./.claude` are not
  loaded and the agent's capabilities are defined by the adapter. Pass
  `("user", "project")` to opt back in.
- **`permission_mode` is a `ClaudePermissionMode`.** The enum names the
  [Claude Code permission modes](https://code.claude.com/docs/en/permission-modes),
  and a test pins it to `claude_agent_sdk`'s `PermissionMode`, so it cannot
  drift from the SDK. The mode is forwarded to the CLI as given; a config
  loaded from text may spell it as its string value. `effort` and
  `setting_sources` are typed as the SDK's own `EffortLevel` and
  `SettingSource` literals. `approvals` sends every native tool call to a
  prompt, which changes two modes:
  - `DONT_ASK` denies every prompt without asking the adapter, so it raises
    `ValueError` with any `approvals`.
  - `AUTO` has its classifier answer prompts only when `approvals` is `None`.
    Prompts forced by the adapter's approval hook skip the classifier, so with
    `approvals` set, that approval policy decides instead.

  When the account or model can't run `AUTO`, the CLI starts the session in
  `AUTO_FALLBACK_PERMISSION_MODE` (`DEFAULT`), and the adapter logs a warning.
- **`turn_timeout_s` bounds a turn.** On expiry the turn is interrupted and a
  `timeout` failure is posted to the room; `None` (default) leaves turns
  unbounded. A manual approval wait counts toward it, so
  `approvals.wait_timeout_s` must be shorter.
- **CLI launch options never override the adapter's own wiring.**
  `cli=ClaudeCLIOptions(...)` sets the executable, plugin folders, extra
  directories, env and extra flags, but cannot replace the Band MCP server, the
  tool allowlist or `setting_sources`: an `extra_args` flag in
  `RESERVED_CLI_FLAGS` raises `ValueError`, as does a dash-prefixed key. That
  covers every flag the adapter sets (use its typed option instead) plus flags
  such as `settings`, `agents`, `bare` and `dangerously-skip-permissions` that
  would bypass Band's wiring, permission gating or host-config isolation. `env`
  reaches only the Claude CLI process; the host's `os.environ` is untouched.

```python
import pytest

from band.adapters.claude_sdk import (
    ClaudeApprovalOptions,
    ClaudeCLIOptions,
    ClaudePermissionMode,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
)

config = ClaudeSDKAdapterConfig(
    model="opus",
    permission_mode=ClaudePermissionMode.ACCEPT_EDITS,
    turn_timeout_s=1800,
    cli=ClaudeCLIOptions(extra_args={"debug-to-stderr": None}),
    approvals=ClaudeApprovalOptions(mode="manual", wait_timeout_s=300),
)
adapter = ClaudeSDKAdapter(config)
assert adapter.config.approvals.mode == "manual"

# A misspelt mode or setting is refused when the config is built.
with pytest.raises(ValueError, match="permission_mode"):
    ClaudeSDKAdapterConfig(permission_mode="dontask")

with pytest.raises(ValueError, match="adapter-owned CLI flags"):
    ClaudeCLIOptions(extra_args={"mcp-config": "{}"})
with pytest.raises(ValueError, match="wait_timeout_s must be less than"):
    ClaudeSDKAdapterConfig(
        turn_timeout_s=60, approvals=ClaudeApprovalOptions(mode="manual")
    )
```

A config is plain data, so a host can load it from YAML or JSON with
`ClaudeSDKAdapterConfig.model_validate(data)`.

An ordinary input arriving while accepted work owns the session remains
outstanding. Approval and status commands stay reachable through the shared
control hook under the existing sender and token checks. The active turn keeps
its original tools; handling a control never rebinds them to the new message.
Only a local rejection before `query()` qualifies as `InputNotAccepted`:
transport errors and timeouts do not establish that Claude rejected the input.
The shared room scheduler owns later attempts.
