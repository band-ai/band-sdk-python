# Claude Agent SDK Adapter

`ClaudeSDKAdapter` runs Claude Code as a subprocess, one session per Band room.
Runnable scripts: [examples/claude_sdk/](../../examples/claude_sdk/).

- **Two credentials.** `Agent.create(api_key=...)` is the Band key only. Claude
  Code authenticates itself (`claude auth login` or `ANTHROPIC_API_KEY`); the
  adapter never hands it a key.
- **Assistant text is never posted.** The adapter only debug-logs it. A reply
  reaches the room through the `band_send_message` tool, and a turn that ends
  with no successful reply or action tool call is reported to the room as an
  error.
- **`approval_mode` gates everything except Band's own tools.** With it set,
  every tool call that is not `mcp__band__*` or `ToolSearch` goes through the
  adapter's approval callback (`"manual"` asks the room). Band's tools are never
  gated, in any mode.
- **Host Claude Code config is ignored.** `setting_sources` defaults to `[]`, so
  skills, subagents and settings under `~/.claude` and `./.claude` are not
  loaded and the agent's capabilities are defined by the adapter. Pass
  `["user", "project"]` to opt back in.
- **`permission_mode` is forwarded to the CLI as given.** It takes the
  [Claude Code permission modes](https://code.claude.com/docs/en/permission-modes)
  by config value. `approval_mode` sends every native tool call to a prompt,
  which changes two modes:
  - `"dontAsk"` denies every prompt without asking the adapter, so it raises
    `ValueError` with any `approval_mode`.
  - `"auto"` has its classifier answer prompts only when `approval_mode` is
    `None`. Prompts forced by the adapter's approval hook skip the classifier,
    so with an `approval_mode` that approval policy decides instead.

  When the account or model can't run `"auto"`, the CLI starts the session in
  `"default"`, and the adapter logs a warning.
- **`turn_timeout_s` bounds a turn.** On expiry the turn is interrupted and a
  `timeout` failure is posted to the room; `None` (default) leaves turns
  unbounded. A manual approval wait counts toward it, so keep it above
  `approval_wait_timeout_s`.
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

from band.adapters.claude_sdk import ClaudeCLIOptions, ClaudeSDKAdapter

adapter = ClaudeSDKAdapter(cli=ClaudeCLIOptions(extra_args={"debug-to-stderr": None}))
assert adapter.cli.extra_args == {"debug-to-stderr": None}

with pytest.raises(ValueError, match="adapter-owned CLI flags"):
    ClaudeCLIOptions(extra_args={"mcp-config": "{}"})
```
