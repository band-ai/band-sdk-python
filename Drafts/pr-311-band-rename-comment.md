# Draft PR comment — PR #311 (feat!: rebrand Python SDK package to Band)

> Paste into the PR #311 conversation. Edit freely before posting.

---

Ran the QA harness end-to-end against the rebranded `band` surface (live, `app.band.ai`) to confirm the rename didn't break runtime behavior. Summary + one fix I pushed onto this branch + one follow-up.

### ✅ Rebrand validated at runtime
The harness was aligned to the new surface (`from band …`, `BAND_*` env, `band_*` platform tools, `band-acp`/`band-trigger`, vendored `band_rest` + `band_testing`) and exercised real agents against the platform:

- `import band` / `band.adapters` / `band_rest` / `band_testing` all load; `thenvoi` is gone.
- Core conversation (greeting → Q&A → context → platform tools → farewell) passes for **langgraph, crewai, parlant, anthropic, pydantic_ai, gemini, google_adk, claude_sdk** (and **letta** via a local Letta+band-mcp stack).
- `band_*` tool calls (`band_send_message`, `band_get_participants`, `band_lookup_peers`, `band_store_memory`, …) execute and the events land on the platform with the new names.

Remaining PARTIALs in the sweep are pre-existing platform/agent behaviors (cross-room memory visibility `PLT-915`; per-adapter rehydration replay), not rename regressions.

### 🩹 Bundled a small claude_sdk fix (`3a813b5`)
Surfaced while QA-testing the rebranded SDK. Not caused by the rename, but it makes claude_sdk agents actually respond, so I cherry-picked it onto this branch:

- **Symptom:** a claude_sdk agent posted only its "Claude SDK session" task event and never replied.
- **Cause:** under **API-key auth**, the npm `claude` CLI auto-selects its default model and sends the legacy `thinking.type.enabled` request shape, which current models reject (`"…not supported for this model. Use thinking.type.adaptive"`). The query returns an error result in ~400ms / $0.00 with no assistant turn.
- **Fix:** pin a default model (`claude-sonnet-4-6`) when the caller doesn't specify one — consistent with the `codex`/`anthropic` adapters, which already default their model. Overridable via `model=`.
- **Verified:** claude_sdk core A/B/C go from failing to all PASS; adapter unit tests 85/85; ruff + pyrefly clean.

### 🔭 Follow-up (separate repo, not blocking this PR)
`band-mcp`'s rename branch (`rename-band-mcp`) still imports the **pre-rebrand `thenvoi`** SDK in its SDK-driven tool registrar, so it logs `No module named 'thenvoi'` and falls back to legacy handlers that don't include the **memory/contacts** tool groups. Effect: a self-hosted Letta agent can't be served real band-memory tools through band-mcp yet. Worth a matching `thenvoi → band` pass on that repo so the import resolves (and the SDK is on its venv). The in-process MCP path (claude_sdk) already exercises band memory tools fine, so this is band-mcp-side only.

🛠️
