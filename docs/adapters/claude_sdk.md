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
- **`permission_mode` is forwarded to the CLI as given.** `"dontAsk"` and
  `"auto"` are accepted; if the CLI rejects a mode (for example `"auto"` on an
  account without it), the turn fails with no fallback to another mode.
- **CLI launch options never override the adapter's own wiring.**
  `cli=ClaudeCLIOptions(...)` sets the executable, plugin folders, extra
  directories, env and extra flags, but cannot replace the Band MCP server, the
  tool allowlist or `setting_sources`: an `extra_args` flag in
  `RESERVED_CLI_FLAGS` raises `ValueError`, as does a dash-prefixed key. `env`
  reaches only the Claude CLI process; the host's `os.environ` is untouched.

```python
import pytest

from band.adapters.claude_sdk import ClaudeCLIOptions, ClaudeSDKAdapter

adapter = ClaudeSDKAdapter(cli=ClaudeCLIOptions(extra_args={"debug-to-stderr": None}))
assert adapter.cli.extra_args == {"debug-to-stderr": None}

with pytest.raises(ValueError, match="adapter-owned CLI flags"):
    ClaudeCLIOptions(extra_args={"mcp-config": "{}"})
```
