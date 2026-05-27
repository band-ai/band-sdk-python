# How to Read and File Reports

## Report Files

Reports are written to `tests/qa/reports/` (gitignored). Each run produces:

| File | Contents |
|------|----------|
| `<adapter>_<example>.md` | Per-example report (core scenarios A-C) |
| `<adapter>_expanded.md` | Expanded scenario results (E-I) |
| `<adapter>_multi_participant.md` | Scenario D results |
| `<adapter>_summary.md` | One-line status per report |
| `cross_adapter_summary.md` | All-adapters sweep results (from `--all-adapters`) |

### Report Contents

Each report contains:
- **Summary:** overall status, date, platform, LLM, agent ID, startup status
- **Scenario Results:** per-scenario table with step-by-step action/expected/actual/status
- **Errors/Warnings:** collected during the run
- **Startup Logs:** first 30 lines of agent stdout/stderr

### Status Meanings

| Status | Meaning |
|--------|---------|
| PASS | All steps passed |
| PARTIAL | Some steps failed but the scenario partially worked |
| FAIL | All steps failed or a critical error occurred |
| SKIP | Scenario skipped (missing credentials or not applicable) |

## Filing Issues

When a scenario fails, determine the root cause layer:

### SDK / Adapter Bug

Agent starts but produces wrong output, crashes during tool use, or fails to handle a platform event.

File under the SDK repo. Include: adapter name, scenario ID, step that failed, agent logs, expected vs actual. Tag with the adapter integration ticket (e.g. `INT-509`).

### Platform Bug

REST API returns unexpected errors, WebSocket events are missing or malformed, messages aren't delivered.

File as a platform issue (`PLT-*`). Include: API endpoint, request/response, timestamps.

### Example Bug

The example file itself has a bug (wrong config key, missing import, hardcoded value).

Fix in the example and re-run.

### LLM Quality Issue

Agent responds but the answer is wrong, off-topic, or doesn't use the right tool.

Usually not a bug — adjust the prompt/custom_section in the agent script. If the LLM consistently fails on a specific tool, that may indicate the tool schema or description needs improvement.

### Known Issues

| Issue | Impact |
|-------|--------|
| PLT-915: `list_memories` returns empty without explicit `subject_id` | Expect PARTIAL on memory list steps |
| Contact scenarios (F2, F3) timing-sensitive | WebSocket event delivery timing varies. If FAIL, increase sleep in scenario. |
| Lookup peers (A step 5) | Some LLMs take >120s to process results. PARTIAL is acceptable. |
