# QA Report: letta / 01_basic_agent

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** openai/gpt-5.4-mini
- **Agent ID:** 5a0f8640-d7a9-4a2c-900f-8807404462f7
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PARTIAL
**Room:** `1b1fd810-0ea2-4a16-ad32-a1b81112accc`
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] 2 + 2 equals 4. | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Hello, Nir! I am QA-Letta-letta_agent-1921, your assistant | FAIL |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Hello, Nir! I am QA-Letta-letta_agent-1921, your assistant | PASS |
| 4 | Send: List participants | Agent uses band_get_participants or describes participants | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I've successfully introduced myself in the chat. If you ha | PASS |
| 5 | Send: Lookup peers | Agent uses band_lookup_peers or responds about peers | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The capital of France is Paris. If you have any more quest | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I've answered your question: the capital of France is Pari | PASS |

### B: Agent Rehydration
**Status:** PARTIAL
**Room:** `1b1fd810-0ea2-4a16-ad32-a1b81112accc`
*Kill and restart the agent, verify it re-joins and responds in the same room*

> Error: Unsolicited messages after restart: 7 extra message(s) replayed from rehydrated history

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The second-largest city in France is Marseille. If you hav | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 1 new message(s) (1 pending + 0 in-flight) | FAIL: 8 new message(s) — 7 unsolicited beyond 1 allowed | FAIL |
| 5 | Post-restart recall (message sent while down) | Agent recalls 'zeppelin' from transcript history | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I've noted the word 'zeppelin' for future reference. If yo | PASS |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation including 'zeppelin' | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I've noted the word 'zeppelin' for future reference. If yo | PASS |
| 7 | No extra responses after recall | 0 extra text messages | FAIL: 1 extra text response(s) | FAIL |

### C: Context Isolation
**Status:** PARTIAL
**Room:** `1b1fd810-0ea2-4a16-ad32-a1b81112accc`
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=8811a143-b6b7-41e2-953f-1d729271257c | PASS |
| 2 | Add agent to new room | Agent added as participant | Added 5a0f8640-d7a9-4a2c-900f-8807404462f7 | PASS |
| 3 | Ask about previous conversation | Agent responds with no prior context | NO RESPONSE | FAIL |
| 4 | Normal question in new room | Agent answers | NO RESPONSE | FAIL |

## Chat Rooms
- **A: Basic Conversation**: `1b1fd810-0ea2-4a16-ad32-a1b81112accc`
- **B: Agent Rehydration**: `1b1fd810-0ea2-4a16-ad32-a1b81112accc`
- **C: Context Isolation**: `1b1fd810-0ea2-4a16-ad32-a1b81112accc`

## Startup Logs (excerpt)
```
[stderr] 2026-06-03 21:14:15 [INFO] band.adapters.letta: Found existing MCP server 'band' (id=mcp_server-8e123fd8-b260-432e-81ea-c6594220c3cf)
[stderr] 2026-06-03 21:14:15 [INFO] band.adapters.letta: Discovered 22 MCP tools: ['add_agent_chat_participant', 'add_agent_contact', 'create_agent_chat', 'create_agent_chat_event', 'create_agent_chat_message', 'get_agent_chat', 'get_agent_chat_context', 'get_agent_me', 'get_agent_next_message', 'health_check', 'list_agent_chat_participants', 'list_agent_chats', 'list_agent_contact_requests', 'list_agent_contacts', 'list_agent_messages', 'list_agent_peers', 'mark_agent_message_failed', 'mark_agent_message_processed', 'mark_agent_message_processing', 'remove_agent_chat_participant', 'remove_agent_contact', 'respond_to_agent_contact_request']
[stderr] 2026-06-03 21:14:15 [INFO] band.adapters.letta: Letta adapter started for agent: QA-Letta-letta_agent-1921 (mode=per_room)
[stderr] 2026-06-03 21:14:15 [INFO] band.runtime.runtime: Starting AgentRuntime for agent 5a0f8640-d7a9-4a2c-900f-8807404462f7
[stderr] 2026-06-03 21:14:15 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_control:5a0f8640-d7a9-4a2c-900f-8807404462f7
[stderr] 2026-06-03 21:14:15 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_control:5a0f8640-d7a9-4a2c-900f-8807404462f7
[stderr] 2026-06-03 21:14:15 [INFO] band.platform.link: Connected to platform
[stderr] 2026-06-03 21:14:15 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:5a0f8640-d7a9-4a2c-900f-8807404462f7
```
