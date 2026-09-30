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
  `"default"`. The adapter logs a warning and `/status` shows the mode in force.
