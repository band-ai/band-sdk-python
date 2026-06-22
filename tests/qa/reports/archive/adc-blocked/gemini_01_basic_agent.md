# QA Report: gemini / 01_basic_agent

## Summary
- **Status:** FAIL
- **Date:** 2026-06-10
- **Platform:** app.band.ai
- **LLM:** gemini-2.5-flash
- **Agent ID:** 8489a271-1404-4b62-90d3-bb97713d8882
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** FAIL
**Room:** `0966e205-a991-4b98-a5ee-a054d8606216`
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
**Status:** FAIL
**Room:** `0966e205-a991-4b98-a5ee-a054d8606216`
*Kill and restart the agent, verify it re-joins and responds in the same room*

> Error: [Errno 8] nodename nor servname provided, or not known

### C: Context Isolation
**Status:** FAIL
**Room:** `0966e205-a991-4b98-a5ee-a054d8606216`
*Create a new room, verify no context leaks from previous conversations*

> Error: Failed to create new room: [Errno 8] nodename nor servname provided, or not known

## Chat Rooms
- **A: Basic Conversation**: `0966e205-a991-4b98-a5ee-a054d8606216`
- **B: Agent Rehydration**: `0966e205-a991-4b98-a5ee-a054d8606216`
- **C: Context Isolation**: `0966e205-a991-4b98-a5ee-a054d8606216`

## Startup Logs (excerpt)
```
[stderr] 2026-06-10 18:20:25 [INFO] band.adapters.gemini: Gemini adapter started for agent: QA-Gem-gemini_agent-1915
[stderr] 2026-06-10 18:20:25 [INFO] band.runtime.runtime: Starting AgentRuntime for agent 8489a271-1404-4b62-90d3-bb97713d8882
[stderr] 2026-06-10 18:20:26 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_control:8489a271-1404-4b62-90d3-bb97713d8882
[stderr] 2026-06-10 18:20:26 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_control:8489a271-1404-4b62-90d3-bb97713d8882
[stderr] 2026-06-10 18:20:26 [INFO] band.platform.link: Connected to platform
[stderr] 2026-06-10 18:20:26 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:8489a271-1404-4b62-90d3-bb97713d8882
```
