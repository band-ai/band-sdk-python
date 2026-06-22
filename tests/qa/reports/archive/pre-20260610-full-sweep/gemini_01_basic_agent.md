# QA Report: gemini / 01_basic_agent

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** gemini-2.5-flash
- **Agent ID:** 8489a271-1404-4b62-90d3-bb97713d8882
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PASS
**Room:** `e5686881-8f93-42fb-91e4-5d807853c2a6`
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Hello @nir.singherTest! I am QA-Gem-gemini_agent-1915. I'm | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The capital of France is Paris. | PASS |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The second-largest city in France is Marseille. | PASS |
| 4 | Send: List participants | Agent uses band_get_participants or describes participants | The current participants in this chat room are: - Nir Singher Test (@[[6d8e9293-5939-45b9-9de9-8742b | PASS |
| 5 | Send: Lookup peers | Agent uses band_lookup_peers or responds about peers | Here are some of the other agents and users available on the platform: - ADK QA Agent (@[[6d8e9293-5 | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] You're very welcome! Goodbye, and have a great day! | PASS |

### B: Agent Rehydration
**Status:** PARTIAL
**Room:** `e5686881-8f93-42fb-91e4-5d807853c2a6`
*Kill and restart the agent, verify it re-joins and responds in the same room*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Understood! I will remember the word 'kaleidoscope'. Feel  | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 1 new message(s) (1 pending + 0 in-flight) | 0 new message(s), 0 unsolicited | PASS |
| 5 | Post-restart recall (message sent while down) | Agent responds to pending message | NO RESPONSE | FAIL |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation including 'kaleidoscope' | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Before the restart, we covered a few things:  1.  I introd | PASS |
| 7 | No extra responses after recall | 0 extra messages | No extra responses | PASS |

### C: Context Isolation
**Status:** PASS
**Room:** `e5686881-8f93-42fb-91e4-5d807853c2a6`
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=0d5defed-c627-4fe0-b790-cefe46d08654 | PASS |
| 2 | Add agent to new room | Agent added as participant | Added 8489a271-1404-4b62-90d3-bb97713d8882 | PASS |
| 3 | Ask about previous conversation | No content from other rooms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I'm sorry, I don't have access to past conversation histor | PASS |
| 4 | Normal question in new room | Agent answers correctly (4) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] 2 + 2 = 4. | PASS |

## Chat Rooms
- **A: Basic Conversation**: `e5686881-8f93-42fb-91e4-5d807853c2a6`
- **B: Agent Rehydration**: `e5686881-8f93-42fb-91e4-5d807853c2a6`
- **C: Context Isolation**: `e5686881-8f93-42fb-91e4-5d807853c2a6`

## Startup Logs (excerpt)
```
[stderr] 2026-06-03 23:47:51 [INFO] band.adapters.gemini: Gemini adapter started for agent: QA-Gem-gemini_agent-1915
[stderr] 2026-06-03 23:47:51 [INFO] band.runtime.runtime: Starting AgentRuntime for agent 8489a271-1404-4b62-90d3-bb97713d8882
[stderr] 2026-06-03 23:47:52 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_control:8489a271-1404-4b62-90d3-bb97713d8882
[stderr] 2026-06-03 23:47:52 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_control:8489a271-1404-4b62-90d3-bb97713d8882
[stderr] 2026-06-03 23:47:52 [INFO] band.platform.link: Connected to platform
[stderr] 2026-06-03 23:47:52 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:8489a271-1404-4b62-90d3-bb97713d8882
[stderr] 2026-06-03 23:47:52 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_rooms:8489a271-1404-4b62-90d3-bb97713d8882
```
