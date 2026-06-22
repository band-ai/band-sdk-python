# QA Report: pydantic_ai / 02_custom_instructions

## Summary
- **Status:** PARTIAL
- **Date:** 2026-05-31
- **Platform:** app.band.ai
- **LLM:** openai:gpt-5.4-mini
- **Agent ID:** c1172de7-dc42-496c-9bbb-a24e2f3f5594
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** FAIL
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | NO RESPONSE (timeout) | FAIL |
| 2 | Send: Domain question | Agent answers Paris | NO RESPONSE (timeout) | FAIL |
| 3 | Send: Follow-up (context) | Agent references France context | NO RESPONSE (timeout) | FAIL |
| 4 | Send: List participants | Agent uses thenvoi_get_participants or describes participants | NO RESPONSE (timeout) | FAIL |
| 5 | Send: Lookup peers | Agent uses thenvoi_lookup_peers or responds about peers | NO RESPONSE (timeout) | FAIL |
| 6 | Send: Goodbye | Agent responds with farewell | NO RESPONSE (timeout) | FAIL |

### B: Agent Rehydration
**Status:** PARTIAL
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
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=776d86dd-ec85-4cea-bb97-3e11786a1b23 | PASS |
| 2 | Add agent to new room | Agent added as participant | Added c1172de7-dc42-496c-9bbb-a24e2f3f5594 | PASS |
| 3 | Ask about previous conversation | Agent responds with no prior context | NO RESPONSE | FAIL |
| 4 | Normal question in new room | Agent answers | NO RESPONSE | FAIL |

## Chat Rooms
- **A: Basic Conversation**: `924ff932-6d41-4f8e-b25d-6eaf3ea0523b`
- **B: Agent Rehydration**: `924ff932-6d41-4f8e-b25d-6eaf3ea0523b`
- **C: Context Isolation**: `924ff932-6d41-4f8e-b25d-6eaf3ea0523b`

## Startup Logs (excerpt)
```
[stderr] 2026-05-31 11:46:22 [INFO] thenvoi.adapters.pydantic_ai: Pydantic AI adapter started for agent: QA-PAI-pydantic_agent-1921
[stderr] 2026-05-31 11:46:22 [INFO] thenvoi.runtime.runtime: Starting AgentRuntime for agent c1172de7-dc42-496c-9bbb-a24e2f3f5594
[stderr] 2026-05-31 11:46:23 [INFO] thenvoi.platform.link: Connected to platform
[stderr] 2026-05-31 11:46:23 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:c1172de7-dc42-496c-9bbb-a24e2f3f5594
[stderr] 2026-05-31 11:46:23 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribed to topic: agent_rooms:c1172de7-dc42-496c-9bbb-a24e2f3f5594
[stderr] 2026-05-31 11:46:23 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:e3962dc0-cdca-4b7f-bc96-24ecd59f3b64
[stderr] 2026-05-31 11:46:23 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:7afe838a-a9c2-49c1-86ba-e3bb6d873211
[stderr] 2026-05-31 11:46:23 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:c52186ea-9eb7-4ee1-87bc-4984e5610a78
[stderr] 2026-05-31 11:46:23 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:7a25751b-610c-47af-8a9c-c4c38a36a5f3
[stderr] 2026-05-31 11:46:23 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:1b8a288f-0190-4b3f-b183-3f2fe82a5731
[stderr] 2026-05-31 11:46:23 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:510e7b9d-024e-4e32-aa2f-82cef6fe3766
[stderr] 2026-05-31 11:46:23 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:01d77fa9-575b-425a-89cc-e112e1df4b6b
[stderr] 2026-05-31 11:46:23 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:d2363d8e-3878-4509-a203-9b3c68d7fdc0
[stderr] 2026-05-31 11:46:23 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:aaf26466-c1be-4b44-a2a3-7247205833b3
```
