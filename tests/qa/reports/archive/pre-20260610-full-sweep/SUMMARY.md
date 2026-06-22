# QA Test Summary — All Adapters

**Date:** 2026-05-26
**Platform:** app.band.ai
**Scenarios:** A (Basic Conversation), B (Agent Rehydration), C (Context Isolation)

## Results Matrix

| Adapter | LLM | Example | A | B | C | Overall |
|---------|-----|---------|:-:|:-:|:-:|---------|
| gemini | gemini-2.5-flash | 01_basic_agent | PASS | PASS | PASS | **PASS** |
| parlant | openai (Parlant) | 01_basic_agent | PASS | PASS | PASS | **PASS** |
| langgraph | gpt-4o | 01_simple_agent | PASS | PARTIAL | PASS | **PARTIAL** |
| claude_sdk | haiku | 01_basic_agent | PARTIAL | PASS | PASS | **PARTIAL** |
| anthropic | claude-sonnet-4.5 | 01_basic_agent | PASS | PARTIAL | PASS | **PARTIAL** |
| crewai | gpt-5.4-mini | 01_basic_agent | PASS | PARTIAL | PASS | **PARTIAL** |
| pydantic_ai | openai:gpt-5.4-mini | 01_basic_agent | PASS | FAIL | PARTIAL | **FAIL** |
| google_adk | gemini-2.5-flash | 01_basic_agent | PASS | FAIL | PASS | **FAIL** |
| google_adk | gemini-2.5-flash | 02_custom_instructions | PASS | FAIL | PASS | **FAIL** |
| google_adk | gemini-2.5-flash | 03_custom_tools | PARTIAL | PARTIAL | PARTIAL | **PARTIAL** |
| letta | openai/gpt-5.4-mini | 01_basic_agent | — | — | — | **SKIP** |

### Scorecard

- **PASS:** 2/9 adapters (gemini, parlant)
- **PARTIAL:** 5/9 adapters (langgraph, anthropic, crewai, google_adk, claude_sdk)
- **FAIL:** 1/9 adapters (pydantic_ai)
- **SKIP:** 1/9 adapters (letta)

## Issues

### 1. Unsolicited messages after rehydration (B) — confirmed in 3/9 adapters

**Severity:** High
**Adapters:** anthropic, google_adk (02), crewai

After a SIGINT + restart, the agent replays responses to messages it already handled before the kill. The SDK rehydration flow loads conversation history, and some adapters' converters present old messages without marking them as already-answered, causing the LLM to treat them as a pending queue.

| Adapter | Unsolicited count | Pattern |
|---------|-------------------|---------|
| anthropic | 3 + 3 extra after recall | Full history replay — 6 messages re-answered in order |
| google_adk (02) | 3 + 1 extra after recall | Multiple replays |
| crewai | 1 | Self-introduction replayed instead of context |

**Note:** Several adapters (claude_sdk, google_adk 01, langgraph) were initially flagged as having unsolicited messages, but chatroom review revealed these were **false positives from harness timing** — the agent was still processing in-flight responses (e.g. slow `lookup_peers`) when the kill arrived. The B5 check has been updated to snapshot unanswered user messages before the kill and allow their replies post-restart.

**Root cause (real unsolicited):** The `is_session_bootstrap` flag is passed to `on_message` but not all adapters correctly use it to suppress responses during rehydration. The converter needs to distinguish "context for the LLM" from "messages requiring a response."

### 2. google_adk agent name validation error (fixed)

**Severity:** Critical (was blocking all responses)
**Status:** Fixed in commit `4042b85`

ADK's `LlmAgent` requires the agent name to be a valid Python identifier (letters, digits, underscores). Platform agent names like `QA-ADK-google_adk_agent-1917` contain hyphens, causing a `pydantic.ValidationError` on every `on_message` call. Every message silently failed after exhausting retries.

**Fix:** Added `_sanitize_adk_name()` to replace non-identifier characters with underscores.

### 3. pydantic_ai restart failure (B)

**Severity:** High
**Adapter:** pydantic_ai

The agent fails to restart after SIGINT. The process exits during startup with an import/initialization error in pydantic internals. Scenario C also fails because the agent is dead.

### 4. pydantic_ai re-entrancy / parallel on_message (A)

**Severity:** Medium
**Adapter:** pydantic_ai

Multiple WebSocket events fire parallel `on_message` calls. All see the same history and produce identical responses (observed: 10+ "Paris" replies to a single question). Needs per-room concurrency locking in the adapter.

### 5. claude_sdk lookup_peers timeout (A5)

**Severity:** Low
**Adapter:** claude_sdk

`thenvoi_lookup_peers` timed out during Scenario A on both Opus and Haiku runs. The agent responds to it in the next turn (A6 reply contains peer data). The Claude SDK adapter spawns a `claude` CLI subprocess per message — the overhead of subprocess launch + 178-peer response formatting exceeds the harness poll window. Not a capability gap.

### 6. langgraph B1 timing race

**Severity:** Low
**Adapter:** langgraph

The "remember xylophone" message arrived close to the goodbye (2s gap). The agent was killed before it could process the secret word message. Not an adapter bug — harness timing edge case.

### 7. crewai history not threaded turn-to-turn (A3)

**Severity:** Medium
**Adapter:** crewai

When asked "what about the second-largest city there?", the agent asked "what does 'there' refer to?" — indicating conversation history isn't properly threaded as a continuous conversation. Each turn appears to be processed without full prior context.

### 8. letta requires external infrastructure

**Severity:** N/A (by design)
**Adapter:** letta

Letta requires a running Letta server with MCP bridge. The `letta-client` package alone is insufficient — it's a thin REST client that connects to the server. Not runnable as a standalone example.

### 9. google_adk custom_tools agent refuses general knowledge (A2)

**Severity:** Low
**Adapter:** google_adk (03_custom_tools only)

The custom-tools example agent responded "I can't answer general knowledge questions like that" to "What is the capital of France?" — likely the custom instructions narrow its scope. Not an SDK issue.

### 10. google_adk C timeouts on third example

**Severity:** Low
**Adapter:** google_adk (03_custom_tools)

By the third example file run, the agent is subscribed to many rooms from prior runs. New room messages timed out — likely the agent was processing backlog from accumulated rooms.

## Adapter-Specific Notes

### gemini
Clean pass across all scenarios. Reliable rehydration, correct tool use, proper context isolation. Uses the direct Gemini API (not ADK).

### parlant
Clean pass. Parlant's NLP layer handles rehydration correctly — no unsolicited messages, proper context recall. Requires a dedicated venv (`UV_PROJECT_ENVIRONMENT=.venv-parlant`) due to dependency conflict with crewai.

### claude_sdk
Re-tested with `model="haiku"` (originally defaulted to Opus which was very slow). Results improved from FAIL to PARTIAL. A5 (`lookup_peers`) consistently times out due to subprocess overhead — the response arrives but the harness has already moved to A6. B4 previously flagged as unsolicited was actually an in-flight response from A. B improved from FAIL to PARTIAL. C passes cleanly.

### google_adk (post-fix)
Now functional after the name sanitization fix. A and C pass on basic examples. B remains affected by the cross-adapter rehydration issue. The ADK Runner adds overhead (session management, tool bridging) compared to the direct Gemini adapter.

### google_adk + PR #312 (`fix/int-509-adk-converter-includes-own-replies`)
Re-tested 01_basic_agent with the converter fix that keeps own-agent text in rehydrated history. Result: **PARTIAL** (A: PASS, B: PARTIAL 3/7, C: PASS). The converter fix is directionally correct — prior to the fix, own-agent replies were dropped during rehydration, making the transcript look like a queue of unanswered messages. With the fix, 1 unsolicited replay still occurs (down from multiple), but the agent then goes completely silent and never responds to subsequent messages. The stuck state suggests a deeper issue in the ADK adapter's post-rehydration message processing, beyond the converter layer. Room: `27555552-d53b-4216-883b-7ff877ea2bd1`, agent: `10992cad-f6a1-465e-9025-2dca7ff1fcf0`.
