# QA Report: crewai / 01_basic_agent

## Summary
- **Status:** PARTIAL
- **Date:** 2026-05-31
- **Platform:** app.band.ai
- **LLM:** gpt-5.4-mini
- **Agent ID:** c4a10c48-4edf-4a2d-a765-bcf1ba79ef51
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PASS
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | Hello @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] — I’m QA-Crew-crewai_agent-1914, a QA test agent for | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The capital of France is Paris. | PASS |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Could you clarify which place you mean by “there”? Then I  | PASS |
| 4 | Send: List participants | Agent uses thenvoi_get_participants or describes participants | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The chat room currently has 2 participants: - Nir Singher  | PASS |
| 5 | Send: Lookup peers | Agent uses thenvoi_lookup_peers or responds about peers | I looked up the available peers on the platform. There are 183 available peers total, and the first  | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | You're welcome! Goodbye, @[[6d8e9293-5939-45b9-9de9-8742bafd896d]]. | PASS |

### B: Agent Rehydration
**Status:** PARTIAL
*Kill and restart the agent, verify it re-joins and responds in the same room*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Acknowledged — I’ll remember the word “platypus.” | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 1 new message(s) (1 pending + 0 in-flight) | 1 new message(s), 0 unsolicited | PASS |
| 5 | Post-restart recall (message sent while down) | Agent responds to pending message | NO RESPONSE | FAIL |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation including 'platypus' | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I can’t see the pre-restart chat history from here, so I c | PARTIAL |
| 7 | No extra responses after recall | 0 extra messages | No extra responses | PASS |

### C: Context Isolation
**Status:** PASS
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=e54fced3-9150-4cc4-82f5-8e2ecd613c3c | PASS |
| 2 | Add agent to new room | Agent added as participant | Added c4a10c48-4edf-4a2d-a765-bcf1ba79ef51 | PASS |
| 3 | Ask about previous conversation | No content from other rooms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I only see the current task message in this chat. So far,  | PASS |
| 4 | Normal question in new room | Agent answers correctly (4) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] 2 + 2 = 4. | PASS |

## Chat Rooms
- **A: Basic Conversation**: `18e44522-4121-468e-84f5-722ccd4f4480`
- **B: Agent Rehydration**: `18e44522-4121-468e-84f5-722ccd4f4480`
- **C: Context Isolation**: `18e44522-4121-468e-84f5-722ccd4f4480`

## Startup Logs (excerpt)
```
[stderr] 2026-05-31 11:34:41 [INFO] thenvoi.adapters.crewai: CrewAI adapter started for agent: QA-Crew-crewai_agent-1914 (model=gpt-5.4-mini, role=QA-Crew-crewai_agent-1914)
[stderr] 2026-05-31 11:34:41 [INFO] thenvoi.runtime.runtime: Starting AgentRuntime for agent c4a10c48-4edf-4a2d-a765-bcf1ba79ef51
[stderr] 2026-05-31 11:34:41 [INFO] thenvoi.platform.link: Connected to platform
[stderr] 2026-05-31 11:34:41 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:c4a10c48-4edf-4a2d-a765-bcf1ba79ef51
[stderr] 2026-05-31 11:34:41 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribed to topic: agent_rooms:c4a10c48-4edf-4a2d-a765-bcf1ba79ef51
```
