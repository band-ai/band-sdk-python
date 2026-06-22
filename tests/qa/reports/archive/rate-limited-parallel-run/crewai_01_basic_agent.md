# QA Report: crewai / 01_basic_agent

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-10
- **Platform:** app.band.ai
- **LLM:** gpt-5.4-mini
- **Agent ID:** c4a10c48-4edf-4a2d-a765-bcf1ba79ef51
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PARTIAL
**Room:** `1a168ef4-17fd-4191-a2fe-1f17199c2f30`
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | Hello @[[6d8e9293-5939-45b9-9de9-8742bafd896d]]! I’m QA-Crew-crewai_agent-1914, a QA test agent in t | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The capital of France is Paris. | PASS |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I’m missing the location context for “there.” Could you te | PASS |
| 4 | Send: List participants | Agent uses band_get_participants or describes participants | Hi @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] — this chat room currently has 2 participants: 1. Nir S | PASS |
| 5 | Send: Lookup peers | Agent uses band_lookup_peers or responds about peers | NO RESPONSE (timeout) | FAIL |
| 6 | Send: Goodbye | Agent responds with farewell | I found 184 available peers total across 2 pages. Page 1 includes agents like @[[6d8e9293-5939-45b9- | PASS |

### B: Agent Rehydration
**Status:** PASS
**Room:** `1a168ef4-17fd-4191-a2fe-1f17199c2f30`
*Kill and restart the agent, verify it re-joins and responds in the same room*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] You're welcome — goodbye! | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 2 new message(s) (1 pending + 1 in-flight) | 2 new message(s), 0 unsolicited | PASS |
| 5 | Post-restart recall (message sent while down) | Agent recalls 'pineapple' from transcript history | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The word you asked me to remember earlier was **pineapple* | PASS |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation including 'pineapple' | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I don’t have access to prior chat history after the restar | PARTIAL |
| 7 | No extra responses after recall | 0 extra messages | No extra responses | PASS |

### C: Context Isolation
**Status:** PASS
**Room:** `1a168ef4-17fd-4191-a2fe-1f17199c2f30`
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=80dc232a-a908-4d44-ae22-0e7e57b1af86 | PASS |
| 2 | Add agent to new room | Agent added as participant | Added c4a10c48-4edf-4a2d-a765-bcf1ba79ef51 | PASS |
| 3 | Ask about previous conversation | No content from other rooms | I don’t have the full conversation history visible in this room beyond the current message, so I can | PASS |
| 4 | Normal question in new room | Agent answers correctly (4) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] 2 + 2 = 4. | PASS |

## Chat Rooms
- **A: Basic Conversation**: `1a168ef4-17fd-4191-a2fe-1f17199c2f30`
- **B: Agent Rehydration**: `1a168ef4-17fd-4191-a2fe-1f17199c2f30`
- **C: Context Isolation**: `1a168ef4-17fd-4191-a2fe-1f17199c2f30`

## Startup Logs (excerpt)
```
[stderr] 2026-06-10 15:16:32 [INFO] band.adapters.crewai: CrewAI adapter started for agent: QA-Crew-crewai_agent-1914 (model=gpt-5.4-mini, role=QA-Crew-crewai_agent-1914)
[stderr] 2026-06-10 15:16:32 [INFO] band.runtime.runtime: Starting AgentRuntime for agent c4a10c48-4edf-4a2d-a765-bcf1ba79ef51
[stderr] 2026-06-10 15:16:33 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_control:c4a10c48-4edf-4a2d-a765-bcf1ba79ef51
[stderr] 2026-06-10 15:16:33 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_control:c4a10c48-4edf-4a2d-a765-bcf1ba79ef51
[stderr] 2026-06-10 15:16:33 [INFO] band.platform.link: Connected to platform
[stderr] 2026-06-10 15:16:33 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:c4a10c48-4edf-4a2d-a765-bcf1ba79ef51
```
