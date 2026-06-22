# QA Report: crewai / 02_role_based_agent

## Summary
- **Status:** PASS
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** gpt-5.4-mini
- **Agent ID:** c4a10c48-4edf-4a2d-a765-bcf1ba79ef51
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PASS
**Room:** `04c1b83d-a5c3-4caa-a67f-c2e7b068da43`
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Hello! I’m QA-Crew-crewai_agent-1914, an AI research and Q | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Paris is the capital of France. | PASS |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I’m missing the earlier context for what place “there” ref | PASS |
| 4 | Send: List participants | Agent uses band_get_participants or describes participants | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Here’s who is in this chat room: - Nir Singher Test (@[[6d | PASS |
| 5 | Send: Lookup peers | Agent uses band_lookup_peers or responds about peers | {"tool": "band_send_message", "error": "At least one mention is required. Available participants: [' | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | {"tool": "band_send_message", "error": "At least one mention is required. Available participants: [' | PASS |

### B: Agent Rehydration
**Status:** PASS
**Room:** `04c1b83d-a5c3-4caa-a67f-c2e7b068da43`
*Kill and restart the agent, verify it re-joins and responds in the same room*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] You're welcome — glad I could help. Goodbye! | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 3 new message(s) (1 pending + 2 in-flight) | 3 new message(s), 0 unsolicited | PASS |
| 5 | Post-restart recall (message sent while down) | Agent recalls 'xylophone' from transcript history | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Acknowledged — I’ll remember the word “xylophone” for your | PASS |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation including 'xylophone' | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I can help summarize, but I don’t have access to the prior | PARTIAL |
| 7 | No extra responses after recall | 0 extra messages | No extra responses | PASS |

### C: Context Isolation
**Status:** PASS
**Room:** `04c1b83d-a5c3-4caa-a67f-c2e7b068da43`
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=1d411544-ccfa-4057-90bf-239243099fd4 | PASS |
| 2 | Add agent to new room | Agent added as participant | Added c4a10c48-4edf-4a2d-a765-bcf1ba79ef51 | PASS |
| 3 | Ask about previous conversation | No content from other rooms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I can help with that. So far, the only substantive discuss | PASS |
| 4 | Normal question in new room | Agent answers correctly (4) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] 2 + 2 = 4. | PASS |

## Chat Rooms
- **A: Basic Conversation**: `04c1b83d-a5c3-4caa-a67f-c2e7b068da43`
- **B: Agent Rehydration**: `04c1b83d-a5c3-4caa-a67f-c2e7b068da43`
- **C: Context Isolation**: `04c1b83d-a5c3-4caa-a67f-c2e7b068da43`

## Startup Logs (excerpt)
```
[stderr] 2026-06-03 18:41:38 [INFO] band.adapters.crewai: CrewAI adapter started for agent: QA-Crew-crewai_agent-1914 (model=gpt-5.4-mini, role=Research Assistant)
[stderr] 2026-06-03 18:41:38 [INFO] band.runtime.runtime: Starting AgentRuntime for agent c4a10c48-4edf-4a2d-a765-bcf1ba79ef51
[stderr] 2026-06-03 18:41:39 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_control:c4a10c48-4edf-4a2d-a765-bcf1ba79ef51
[stderr] 2026-06-03 18:41:39 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_control:c4a10c48-4edf-4a2d-a765-bcf1ba79ef51
[stderr] 2026-06-03 18:41:39 [INFO] band.platform.link: Connected to platform
[stderr] 2026-06-03 18:41:39 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:c4a10c48-4edf-4a2d-a765-bcf1ba79ef51
```
