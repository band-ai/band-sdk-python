# Copilot-in-VS-Code validation suite (opt-in, dev-machine only)

> ## ⚠️ This is inherently a SEMI-MANUAL test suite
>
> It drives a **real, visible VS Code window** with a **human-signed-in
> Copilot**. A person must be at the machine: sign in to Copilot once, and on
> the **first run** click through a handful of one-time dialogs (folder trust,
> MCP server allow, per-tool "Always allow"). Those choices are remembered, so
> **reruns are unattended** — but the GUI window, the interactive GitHub OAuth,
> and the possibility of a new confirmation dialog never go away. It can never
> run in CI and is not a hands-off matrix suite; treat every green run as
> "passed on a supervised dev machine", and record it via the scorecard
> artifact.

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

**Easiest**: use the directory-scoped Claude Code skill — `/vscode-chat-e2e`
(defined in `.claude/skills/vscode-chat-e2e/SKILL.md` next to this suite). It
checks prerequisites, installs what it can, runs the suite, and tells you
exactly what to click on a first run.

Manual equivalent:

```bash
VSCODE_CHAT_TESTS_ENABLED=true E2E_TESTS_ENABLED=true \
VSCODE_CHAT_SCORECARD_JSON=artifacts/int-1110-copilot-vscode-scorecard.json \
uv run pytest tests/e2e/vscode -v -s --no-cov
```

During the run a VS Code window opens on the scaffolded workspace. The harness
minimizes prompts, and after the first run reruns are unattended:

- **Workspace trust**: suppressed — the window is launched with
  `--disable-workspace-trust` (scoped to that launch, not a global setting).
- **MCP server trust**: VS Code asks once per server *configuration*. The
  workspace path and the band-mcp port are stable by default, so `mcp.json`
  is byte-identical across runs — allow the `band` server on the first run
  and the remembered trust holds for every rerun. (Changing
  `VSCODE_CHAT_WORKSPACE` or `BAND_MCP_PORT` re-triggers the prompt.)
- **Tool approvals**: on each tool's first-ever call, open the dropdown next
  to Allow and pick **"Always allow…" (workspace)** — one click per tool
  (~4 band tools), remembered afterwards. The workspace also sets
  `chat.autoReply` so agent-side questions never stall a turn.
- Leave the window open and unfocused-but-alive until the run finishes.

> **Security note:** the harness deliberately does **not** enable
> `chat.tools.global.autoApprove` ("YOLO mode") — that disables tool approval
> for every workspace on the machine and VS Code escalates it to a global
> consent dialog. Approvals stay per-tool and workspace-scoped. If you enable
> YOLO in your own user settings, that is a machine-wide security trade-off
> you own.

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
| `BAND_MCP_PORT` | `8631` | band-mcp SSE port (`0` = ephemeral; stable keeps MCP trust remembered) |
| `VSCODE_CHAT_WORKSPACE` | `~/band-e2e/vscode-chat-workspace` | Workspace dir VS Code opens (stable keeps MCP trust remembered) |
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
