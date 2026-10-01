# Claude Agent SDK Adapter

`ClaudeSDKAdapter` runs Claude Code as a subprocess, one session per Band room.
Runnable scripts: [examples/claude_sdk/](../../examples/claude_sdk/).

Like the other adapters, it takes one settings object,
`ClaudeSDKAdapterConfig`, plus the keyword-only `additional_tools`,
`history_converter` and feature flags (`emit=`, `capabilities=`, ...). The
config is a frozen pydantic model that refuses unknown fields, so a misspelt
setting fails when the adapter is built rather than being silently ignored.
CLI launch options and chat approvals are nested groups:
`cli=ClaudeCLIOptions(...)` and `approvals=ClaudeApprovalOptions(...)`.

- **Two credentials.** `Agent.create(api_key=...)` is the Band key only. Claude
  Code authenticates itself (`claude auth login` or `ANTHROPIC_API_KEY`); the
  adapter never hands it a key.
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
- **`permission_mode`, `effort` and `setting_sources` take the SDK's own
  values.** They are typed as `claude_agent_sdk`'s `PermissionMode`,
  `EffortLevel` and `SettingSource` literals, so the installed SDK decides what
  is valid: a new mode works without an adapter change, and a typo is refused
  up front. `permission_mode` takes the
  [Claude Code permission modes](https://code.claude.com/docs/en/permission-modes)
  and is forwarded to the CLI as given. `approvals` sends every native tool call
  to a prompt, which changes two modes:
  - `"dontAsk"` denies every prompt without asking the adapter, so it raises
    `ValueError` with any `approvals`.
  - `"auto"` has its classifier answer prompts only when `approvals` is `None`.
    Prompts forced by the adapter's approval hook skip the classifier, so with
    `approvals` set, that approval policy decides instead.

  When the account or model can't run `"auto"`, the CLI starts the session in
  `"default"`, and the adapter logs a warning.
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
from typing import get_args

import pytest
from claude_agent_sdk.types import PermissionMode

from band.adapters.claude_sdk import (
    ClaudeApprovalOptions,
    ClaudeCLIOptions,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
)

config = ClaudeSDKAdapterConfig(
    model="opus",
    permission_mode="acceptEdits",
    turn_timeout_s=1800,
    cli=ClaudeCLIOptions(extra_args={"debug-to-stderr": None}),
    approvals=ClaudeApprovalOptions(mode="manual", wait_timeout_s=300),
)
adapter = ClaudeSDKAdapter(config)
assert adapter.config.approvals.mode == "manual"

# Every mode the installed SDK knows is accepted; anything else is refused.
for mode in get_args(PermissionMode):
    ClaudeSDKAdapterConfig(permission_mode=mode)
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
