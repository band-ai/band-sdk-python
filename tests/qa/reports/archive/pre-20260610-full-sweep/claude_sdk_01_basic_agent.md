# QA Report: claude_sdk / 01_basic_agent

## Summary
- **Status:** PASS
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** claude
- **Agent ID:** 5f643438-4ff1-44bb-b071-33df1149508f
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PASS
**Room:** `ba06c7b2-4454-4298-9c0a-44ee2ec08597`
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Hi! I'm **QA-ClaudeSDK**, a QA test agent for the Band Pyt | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The capital of France is **Paris**. | PASS |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The second-largest city in France is **Marseille**, a majo | PASS |
| 4 | Send: List participants | Agent uses band_get_participants or describes participants | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] There are 2 participants in this room:  1. **Nir Singher T | PASS |
| 5 | Send: Lookup peers | Agent uses band_lookup_peers or responds about peers | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I found available peers on the platform. The first page re | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Goodbye! Feel free to come back anytime. 👋 | PASS |

### B: Agent Rehydration
**Status:** PASS
**Room:** `ba06c7b2-4454-4298-9c0a-44ee2ec08597`
*Kill and restart the agent, verify it re-joins and responds in the same room*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Got it — I'll remember **platypus**. Go ahead and restart  | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 1 new message(s) (1 pending + 0 in-flight) | 1 new message(s), 0 unsolicited | PASS |
| 5 | Post-restart recall (message sent while down) | Agent recalls 'platypus' from transcript history | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The word you asked me to remember was **platypus**! | PASS |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation including 'platypus' | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Here's a summary of our conversation before the restart:   | PASS |
| 7 | No extra responses after recall | 0 extra messages | No extra responses | PASS |

### C: Context Isolation
**Status:** PASS
**Room:** `ba06c7b2-4454-4298-9c0a-44ee2ec08597`
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=64f7a018-6228-43e2-9132-2e67fef5cc8f | PASS |
| 2 | Add agent to new room | Agent added as participant | Added 5f643438-4ff1-44bb-b071-33df1149508f | PASS |
| 3 | Ask about previous conversation | No content from other rooms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] This is the start of our conversation — nothing has been d | PASS |
| 4 | Normal question in new room | Agent answers correctly (4) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] 2 + 2 = 4 | PASS |

## Chat Rooms
- **A: Basic Conversation**: `ba06c7b2-4454-4298-9c0a-44ee2ec08597`
- **B: Agent Rehydration**: `ba06c7b2-4454-4298-9c0a-44ee2ec08597`
- **C: Context Isolation**: `ba06c7b2-4454-4298-9c0a-44ee2ec08597`

## Startup Logs (excerpt)
```
[stderr] 2026-06-03 21:49:46 [INFO] band.adapters.claude_sdk: Band MCP SDK server created with 7 tools (0 custom)
[stderr] 2026-06-03 21:49:46 [INFO] band.integrations.claude_sdk.session_manager: ClaudeSessionManager initialized
[stderr] 2026-06-03 21:49:46 [INFO] band.adapters.claude_sdk: Claude SDK adapter started for agent: QA-ClaudeSDK-claude_sdk_agent-1913 (model=claude-sonnet-4-6, fallback_model=none, thinking=None, approval=None)
[stderr] 2026-06-03 21:49:46 [INFO] band.runtime.runtime: Starting AgentRuntime for agent 5f643438-4ff1-44bb-b071-33df1149508f
[stderr] 2026-06-03 21:49:47 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_control:5f643438-4ff1-44bb-b071-33df1149508f
[stderr] 2026-06-03 21:49:47 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_control:5f643438-4ff1-44bb-b071-33df1149508f
[stderr] 2026-06-03 21:49:47 [INFO] band.platform.link: Connected to platform
[stderr] 2026-06-03 21:49:47 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:5f643438-4ff1-44bb-b071-33df1149508f
```
