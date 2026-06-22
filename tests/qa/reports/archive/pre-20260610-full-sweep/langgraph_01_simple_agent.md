# QA Report: langgraph / 01_simple_agent

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** gpt-4o
- **Agent ID:** 9ad0d7f1-13c8-4113-917c-ca76b76be73f
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PASS
**Room:** `b0fdd467-b094-49d1-aa53-8d783f41cd7b`
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | Hi @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] — I’m QA-LG-simple_agent-1919, a chat assistant for thi | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The capital of France is Paris. | PASS |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The second-largest city in France is usually considered Ma | PASS |
| 4 | Send: List participants | Agent uses band_get_participants or describes participants | Here’s who’s in the chat room: - @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] — Nir Singher Test (owner | PASS |
| 5 | Send: Lookup peers | Agent uses band_lookup_peers or responds about peers | Yes — I found 184 available peers on the platform. A few examples: - @[[6d8e9293-5939-45b9-9de9-8742 | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | You’re welcome, @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] — goodbye! | PASS |

### B: Agent Rehydration
**Status:** PARTIAL
**Room:** `b0fdd467-b094-49d1-aa53-8d783f41cd7b`
*Kill and restart the agent, verify it re-joins and responds in the same room*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | NO RESPONSE | FAIL |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 2 new message(s) (1 pending + 1 in-flight) | 1 new message(s), 0 unsolicited | PASS |
| 5 | Post-restart recall (message sent while down) | Agent recalls 'chrysanthemum' from transcript history | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] You asked me to remember the word “chrysanthemum.” | PASS |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation including 'chrysanthemum' | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Before the restart, we covered: 1) my introduction as a ch | PASS |
| 7 | No extra responses after recall | 0 extra messages | No extra responses | PASS |

### C: Context Isolation
**Status:** PASS
**Room:** `b0fdd467-b094-49d1-aa53-8d783f41cd7b`
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=3c538214-380a-4644-ba40-6bafa85c73b6 | PASS |
| 2 | Add agent to new room | Agent added as participant | Added 9ad0d7f1-13c8-4113-917c-ca76b76be73f | PASS |
| 3 | Ask about previous conversation | No content from other rooms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I only have the current request in context, so there isn’t | PASS |
| 4 | Normal question in new room | Agent answers correctly (4) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] 2 + 2 = 4. | PASS |

## Chat Rooms
- **A: Basic Conversation**: `b0fdd467-b094-49d1-aa53-8d783f41cd7b`
- **B: Agent Rehydration**: `b0fdd467-b094-49d1-aa53-8d783f41cd7b`
- **C: Context Isolation**: `b0fdd467-b094-49d1-aa53-8d783f41cd7b`

## Startup Logs (excerpt)
```
[stderr]    Building band-sdk @ file:///Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness
[stderr]       Built band-sdk @ file:///Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness
[stderr] Uninstalled 1 package in 11ms
[stderr] Installed 2 packages in 3ms
[stderr] 2026-06-03 18:39:48 [INFO] band.adapters.langgraph: LangGraph adapter started for agent: QA-LG-simple_agent-1919
[stderr] 2026-06-03 18:39:48 [INFO] band.runtime.runtime: Starting AgentRuntime for agent 9ad0d7f1-13c8-4113-917c-ca76b76be73f
[stderr] 2026-06-03 18:39:49 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_control:9ad0d7f1-13c8-4113-917c-ca76b76be73f
[stderr] 2026-06-03 18:39:49 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_control:9ad0d7f1-13c8-4113-917c-ca76b76be73f
[stderr] 2026-06-03 18:39:49 [INFO] band.platform.link: Connected to platform
[stderr] 2026-06-03 18:39:49 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:9ad0d7f1-13c8-4113-917c-ca76b76be73f
```
