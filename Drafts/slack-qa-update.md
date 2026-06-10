*SDK QA Test Results — All Adapters*

Ran automated scenarios against every adapter example. Three core scenarios:
- *A: Basic Conversation* — 6-message flow: greeting, domain Q&A, context follow-up, `get_participants`, `lookup_peers`, farewell
- *B: Agent Rehydration* — SIGINT kill, send message while down, restart, verify recall + zero unsolicited replays
- *C: Context Isolation* — new room after B, verify no cross-room context leaks

*Results per adapter:*

| Adapter | LLM | A | B | C |
|---|---|---|---|---|
| gemini | gemini-2.5-flash | PASS | PASS | PASS |
| parlant | openai (Parlant) | PASS | PASS | PASS |
| langgraph | gpt-4o | PASS | PARTIAL | PASS |
| claude_sdk | haiku | PARTIAL | PASS | PASS |
| anthropic | claude-sonnet-4.5 | PASS | PARTIAL | PASS |
| crewai | gpt-5.4-mini | PASS | PARTIAL | PASS |
| pydantic_ai | gpt-5.4-mini | PASS | FAIL | PARTIAL |
| google_adk | gemini-2.5-flash | PASS | FAIL | PASS |
| letta | — | SKIP | SKIP | SKIP |

*PRs in flight (related):*
- #312 — `fix(integrations): keep own-agent text in ADK converted history [INT-509]` — fixes part of google_adk rehydration
- #313 — `fix(integrations): dedupe Claude SDK send_message MCP retries [INT-502]` (draft)
- #319 — `fix(pydantic_ai): output_type=None crash + missing anthropic dep [INT-488]`
- #320 — `fix(sdk): document gcloud ADC auth for Gemini and ADK [INT-519]`

*Issues to open:*
1. *Anthropic adapter rehydration replay* — after restart, replays 6 old messages as if they're new. Converter presents rehydrated history without marking own replies, so LLM treats it as a queue. (high)
2. *CrewAI adapter has no conversation history threading* — each `on_message` appears to lack prior turn context. Agent can't follow up ("what does 'there' refer to?") and can't recall pre-restart conversation at all. (high)
3. *Pydantic AI re-entrancy* — parallel WebSocket events fire concurrent `on_message` calls that all see the same history and produce duplicate responses (10+ identical replies). Needs per-room lock. (high)
4. *Pydantic AI restart crash* — agent fails to restart after SIGINT with pydantic internals error. (medium)
5. *google_adk agent name sanitization* — ADK's `LlmAgent` rejects platform names with hyphens. Already fixed locally, needs PR. (critical — blocks all responses)
6. *Letta example not self-contained* — requires external Letta server + MCP bridge. Example should document this clearly or be moved to an integration guide. (low)
