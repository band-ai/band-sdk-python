# QA Report: gemini / 01_basic_agent

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-10
- **Platform:** app.band.ai
- **LLM:** gemini-2.5-flash
- **Agent ID:** 8489a271-1404-4b62-90d3-bb97713d8882
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** FAIL
**Room:** `323cb055-3332-4732-98c8-7bc4848bfffc`
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | NO RESPONSE (timeout) | FAIL |
| 2 | Send: Domain question | Agent answers Paris | NO RESPONSE (timeout) | FAIL |
| 3 | Send: Follow-up (context) | Agent references France context | NO RESPONSE (timeout) | FAIL |
| 4 | Send: List participants | Agent uses band_get_participants or describes participants | NO RESPONSE (timeout) | FAIL |
| 5 | Send: Lookup peers | Agent uses band_lookup_peers or responds about peers | NO RESPONSE (timeout) | FAIL |
| 6 | Send: Goodbye | Agent responds with farewell | NO RESPONSE (timeout) | FAIL |

### B: Agent Rehydration
**Status:** PARTIAL
**Room:** `323cb055-3332-4732-98c8-7bc4848bfffc`
*Kill and restart the agent, verify it re-joins and responds in the same room*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | NO RESPONSE | FAIL |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 8 new message(s) (1 pending + 7 in-flight) | 0 new message(s), 0 unsolicited | PASS |
| 5 | Post-restart recall (message sent while down) | Agent responds to pending message | NO RESPONSE | FAIL |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation | NO RESPONSE | FAIL |
| 7 | No extra responses after recall | 0 extra messages | No extra responses | PASS |

### C: Context Isolation
**Status:** PARTIAL
**Room:** `323cb055-3332-4732-98c8-7bc4848bfffc`
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=0f5b2930-ebd9-4aad-8387-b685530abe85 | PASS |
| 2 | Add agent to new room | Agent added as participant | Added 8489a271-1404-4b62-90d3-bb97713d8882 | PASS |
| 3 | Ask about previous conversation | Agent responds with no prior context | NO RESPONSE | FAIL |
| 4 | Normal question in new room | Agent answers | NO RESPONSE | FAIL |

## Chat Rooms
- **A: Basic Conversation**: `323cb055-3332-4732-98c8-7bc4848bfffc`
- **B: Agent Rehydration**: `323cb055-3332-4732-98c8-7bc4848bfffc`
- **C: Context Isolation**: `323cb055-3332-4732-98c8-7bc4848bfffc`

## Startup Logs (excerpt)
```
[stderr] 2026-06-10 15:16:32 [INFO] band.adapters.gemini: Gemini adapter started for agent: QA-Gem-gemini_agent-1915
[stderr] 2026-06-10 15:16:32 [INFO] band.runtime.runtime: Starting AgentRuntime for agent 8489a271-1404-4b62-90d3-bb97713d8882
[stderr] 2026-06-10 15:16:33 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_control:8489a271-1404-4b62-90d3-bb97713d8882
[stderr] 2026-06-10 15:16:33 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_control:8489a271-1404-4b62-90d3-bb97713d8882
[stderr] 2026-06-10 15:16:33 [INFO] band.platform.link: Connected to platform
[stderr] 2026-06-10 15:16:33 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:8489a271-1404-4b62-90d3-bb97713d8882
```
