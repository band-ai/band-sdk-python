# Copilot-in-VS-Code validation suite (opt-in, dev-machine only)

Drives **GitHub Copilot Chat inside a real VS Code window** (agent mode) against
the live Band platform and validates the L0–L4 common bar. The harness:

1. provisions a fresh Band agent identity,
2. runs **band-mcp** (SSE, loopback) holding that identity's key,
3. scaffolds a throwaway workspace whose `.vscode/mcp.json` points Copilot at it,
4. opens the workspace window once, then submits each turn via `code chat -m agent`,
5. asserts **Band-side only** (reply capture + LLM judge) — the VS Code UI is
   never scraped.

**This suite can never run in CI**: the Copilot extension authenticates through
interactive GitHub OAuth in a browser (no headless/service-account path), and a
GUI window must stay open. It is gated behind `VSCODE_CHAT_TESTS_ENABLED`
(marker `vscode_chat`), like the `DOCKER_TESTS_ENABLED` pattern.

## Surface semantics (why the tests look like this)

- **No inbound channel.** Copilot in VS Code cannot be pushed a Band message,
  and band-mcp exposes **no message-read/history tool** (verified against
  band-mcp 1.3.2). Every turn is driver-initiated: the prompt relays the room
  message and pins the room `chat_id`. This mirrors real usage, where the
  developer relays context into chat and Copilot posts via `band_send_message`.
- **Recall = platform memory tools.** Cross-session/cross-restart recall flows
  through `band_store_memory` / `band_list_memories` (the server runs with
  `--tools memory`), which are platform-persisted and session-independent.
- **No delivery acks.** band-mcp is REST-only, so the baseline
  `wait_for_reply` barrier (delivery-status PROCESSED) never fires; the cells
  wait on captured room messages instead.
- **L4 usage is N/A**: the surface exposes no per-turn usage/billing signal.
  The scorecard carries the rationale as a fixed `na` row.

## Prerequisites

- **VS Code ≥ 1.102** with the `code` CLI on PATH (macOS: run
  "Shell Command: Install 'code' command in PATH" from the Command Palette),
  or set `CODE_COMMAND`.
- **GitHub Copilot Chat extension installed and signed in** (interactive; do
  this once in the normal VS Code UI before running).
- **band-mcp ≥ 1.3.2** installed (`uv tool install band-mcp`), or set
  `BAND_MCP_COMMAND` (e.g. `"uvx band-mcp"`).
- `.env.test` with `BAND_API_KEY_USER` (provisioning/observer) and
  `ANTHROPIC_API_KEY` (the judge).

## Running

```bash
VSCODE_CHAT_TESTS_ENABLED=true E2E_TESTS_ENABLED=true \
VSCODE_CHAT_SCORECARD_JSON=artifacts/int-1110-copilot-vscode-scorecard.json \
uv run pytest tests/e2e/vscode -v -s --no-cov
```

During the run a VS Code window opens on the scaffolded temp workspace:

- **Accept workspace trust** when prompted (first open).
- If VS Code prompts to **start/trust the `band` MCP server**, allow it.
- If individual tool calls still ask for approval (the workspace sets
  `chat.tools.global.autoApprove`, but the installed VS Code may scope that
  setting user-level only), pick **"Always allow"** on the first occurrence of
  each tool — subsequent turns run unattended.
- Leave the window open and unfocused-but-alive until the run finishes.

> **Security note:** `chat.tools.global.autoApprove` disables per-tool
> confirmation prompts. The workspace is a throwaway temp directory and the
> only configured MCP server is the harness's own band-mcp instance, which
> bounds the exposure — do not reuse this settings file in a real workspace.

Artifacts (when `VSCODE_CHAT_SCORECARD_JSON` is set):

- `artifacts/int-1110-copilot-vscode-scorecard.json` — pass/fail/skip rows plus
  the fixed L4 `na` row (baseline scorecard row schema, merge-compatible).
- `…-scorecard.json.meta.json` — environment evidence: OS, `code --version`,
  Copilot extension versions, band-mcp version.

## Environment knobs

| Env var | Default | Purpose |
|---|---|---|
| `VSCODE_CHAT_TESTS_ENABLED` | `false` | The collection gate |
| `CODE_COMMAND` | `code` | VS Code CLI binary (may be a full path) |
| `BAND_MCP_COMMAND` | `band-mcp` | band-mcp launcher (e.g. `uvx band-mcp`) |
| `VSCODE_CHAT_SCORECARD_JSON` | empty | Scorecard output path (empty = don't emit) |
| `VSCODE_CHAT_TIMEOUT` | `300` | Seconds allowed per live turn |

Band endpoints, credentials, autoclean/orphan-sweep policy, and the judge model
come from the baseline settings (`tests/e2e/baseline/settings.py`).

## Cells

| Test | Level | Proves |
|---|---|---|
| `test_participation_reply_round_trip` | L0 | Room message → Copilot → `band_send_message` reply with the echo token |
| `test_original_functions_retained` | L1 | Native function (workspace file) + platform tool (`band_get_participants`) in one turn |
| `test_multi_participant_echo_peer` | L0 | A peer agent's message drives a turn; the reply engages the peer |
| `test_recall_across_chat_sessions` | L2 | Fact stored via memory tools survives to a fresh chat session |
| `test_no_leak_between_rooms` | L2 | Room B's answer names room B's marker, never room A's |
| `test_restart_recall_and_function` | L3 | band-mcp restart between turns; recall + native function still work |
| *(no test)* `usage_accounting` | L4 | `na` scorecard row — no per-turn usage signal exposed |

## Manual variants / known weak spots

- **Fresh chat session** (`new_session=True`) is expressed as a prompt preamble —
  the `code chat` CLI has no verified per-invocation new-session switch. For a
  stronger variant, click **New Chat** in the window between the two turns of
  `test_recall_across_chat_sessions` while it waits.
- **Full window restart** (quit VS Code between the turns of
  `test_restart_recall_and_function`, reopen with `code <workspace-dir>`) is a
  manual variant; the automated cell restarts the platform bridge (band-mcp)
  instead, which is deterministic.

## Troubleshooting

- **Prompt lands in the wrong window**: `code chat` targets the window whose
  workspace matches the CWD; keep only the harness's workspace window open, or
  close other VS Code windows.
- **421 responses from band-mcp**: the server sets
  `ALLOWED_HOSTS='["localhost:*","127.0.0.1:*"]'` itself; if you changed the
  host/port wiring, keep the allowlist in sync.
- **Turn times out with no reply**: check the window — a pending trust/approval
  dialog blocks the turn. Approve it; the run continues on the next cell.
- **`PreflightError`**: the `code` CLI is missing or predates the `chat`
  subcommand — upgrade VS Code / fix `CODE_COMMAND`.

## Future work

`driver.PromptDriver` is a protocol: a `@vscode/test-electron` backend (pinned
VS Code download, persistent signed-in profile, programmatic
`workbench.action.chat.open`) can replace `CodeChatDriver` without touching the
cells.
