---
name: vscode-chat-e2e
description: Run the semi-manual Copilot-in-VS-Code E2E validation suite (tests/e2e/vscode) end to end — verify prerequisites, auto-install what can be installed (VS Code, band-mcp), start the live run, and coach the human through the few one-time dialogs only they can click (Copilot sign-in, folder trust, MCP/tool approvals). Use when asked to run, rerun, or validate the VS Code Copilot surface, or to produce its scorecard evidence.
---

# Copilot-in-VS-Code E2E run

This suite is **inherently semi-manual** (see `tests/e2e/vscode/README.md`): it
drives a real, visible VS Code window with a human-signed-in Copilot. Your job
is to make the human's part as small as possible: check every prerequisite,
install whatever can be installed non-interactively, run the suite, and tell
the user exactly what to click **only** when there is no other way.

## Phase 1 — prerequisites (install, don't ask, where possible)

Check in this order; fix silently where a non-interactive fix exists.

1. **VS Code + `code` CLI** — `code --version` and `code chat --help`.
   - Missing entirely: `brew install --cask visual-studio-code` (macOS; the
     cask links `code` onto PATH itself).
   - App present but no CLI: link it —
     `ln -sf "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" /opt/homebrew/bin/code`.
   - `chat` subcommand missing: VS Code too old (< 1.101) —
     `brew upgrade --cask visual-studio-code`.
2. **Copilot Chat extension** — ships built into current VS Code; verify with
   `code --install-extension GitHub.copilot-chat` (a "already installed"
   response is success).
3. **band-mcp** — `uvx band-mcp --version` (uvx fetches it on first use; no
   install step). Pass `BAND_MCP_COMMAND="uvx band-mcp"` to the run.
4. **Credentials** — `.env.test` must define `BAND_API_KEY_USER` (provisioning
   + observer) and `ANTHROPIC_API_KEY` (judge). Missing keys cannot be
   installed: stop and ask the user for them.
5. **Copilot sign-in** — cannot be automated (interactive GitHub OAuth, no
   headless path). If unsure whether a login exists, just start Phase 2: a
   signed-out Copilot shows "Sign in to use Copilot" in the chat panel — then
   instruct: *open Chat (⌃⌘I), click "Sign in to use Copilot", finish the
   browser OAuth, then say "signed in"* and rerun.

## Phase 2 — the run

```bash
VSCODE_CHAT_TESTS_ENABLED=true E2E_TESTS_ENABLED=true \
BAND_MCP_COMMAND="uvx band-mcp" \
VSCODE_CHAT_SCORECARD_JSON=artifacts/<issue-or-run-id>-copilot-vscode-scorecard.json \
uv run pytest tests/e2e/vscode -v -s --no-cov
```

Run it in the background and monitor per-cell PASSED/FAILED lines. Kill any
leftover `band-mcp --transport sse` process before starting. Expect ~4 minutes
for 6 cells.

## Phase 3 — coach the one-time dialogs (first run on a machine only)

The workspace (`~/band-e2e/vscode-chat-workspace`) and band-mcp port (8631)
are stable, so every choice below is remembered — reruns are unattended.
When the VS Code window opens, tell the user to click, as dialogs appear:

1. "Do you trust the authors…" → **Trust Folder & Continue**
2. MCP server `band` start/trust → **Allow/Trust**
3. Tool confirmations → dropdown next to Allow → **"Always allow…" (workspace)**
   — once per tool (~4 band tools), never "this session"
4. "Allow edits to sensitive files" (shouldn't appear on the visible-path
   workspace; if it does) → dropdown → **Always allow**

Never tell the user to enable `chat.tools.global.autoApprove` ("YOLO mode") —
machine-wide security downgrade; the harness deliberately avoids it.

## Phase 4 — verdict + evidence

- Green: report `N passed`, point at the scorecard JSON + `.meta.json`
  sidecar (versions evidence). Commit artifacts only when asked.
- A cell failing on reply content is usually live-model flakiness — rerun
  once before investigating; two identical failures = real, read the
  traceback (full failure section prints at session end).
- "Quota reached" badge in the window = Copilot premium requests exhausted;
  turns will stall or degrade. Surface it to the user: paid seat, wait for
  reset, or switch the chat model picker to an included model.
- Reap check: provisioned agents/rooms are auto-reaped (`BAND_E2E_AUTOCLEAN`);
  leftover `band-mcp` processes should be killed if the run was interrupted.
