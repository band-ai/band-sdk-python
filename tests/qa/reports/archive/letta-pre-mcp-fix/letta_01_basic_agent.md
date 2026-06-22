# QA Report: letta / 01_basic_agent

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-10
- **Platform:** app.band.ai
- **LLM:** openai/gpt-5.4-mini
- **Agent ID:** 5a0f8640-d7a9-4a2c-900f-8807404462f7
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PARTIAL
**Room:** `2dddafc7-1656-4f24-8311-d4b8e3049f6c`
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | NO RESPONSE (timeout) | FAIL |
| 2 | Send: Domain question | Agent answers Paris | NO RESPONSE (timeout) | FAIL |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I’m unable to send messages at the moment. Would you like  | PASS |
| 4 | Send: List participants | Agent uses band_get_participants or describes participants | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I am currently experiencing difficulties with sending mess | PASS |
| 5 | Send: Lookup peers | Agent uses band_lookup_peers or responds about peers | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] It seems there is an issue with sending messages due to th | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] It seems there are persistent issues with sending messages | PASS |

### B: Agent Rehydration
**Status:** PARTIAL
**Room:** `2dddafc7-1656-4f24-8311-d4b8e3049f6c`
*Kill and restart the agent, verify it re-joins and responds in the same room*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I've remembered the word **'platypus'** as you requested.  | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 3 new message(s) (1 pending + 2 in-flight) | 0 new message(s), 0 unsolicited | PASS |
| 5 | Post-restart recall (message sent while down) | Agent recalls 'platypus' from transcript history | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] It seems that the system is experiencing some issues with  | PARTIAL |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation | NO RESPONSE | FAIL |
| 7 | No extra responses after recall | 0 extra messages | No extra responses | PASS |

### C: Context Isolation
**Status:** PARTIAL
**Room:** `2dddafc7-1656-4f24-8311-d4b8e3049f6c`
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=e694b6b4-4fc7-45fc-949c-4b66d5a28ce5 | PASS |
| 2 | Add agent to new room | Agent added as participant | Added 5a0f8640-d7a9-4a2c-900f-8807404462f7 | PASS |
| 3 | Ask about previous conversation | Agent responds with no prior context | NO RESPONSE | FAIL |
| 4 | Normal question in new room | Agent answers correctly (4) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] It seems I've encountered persistent issues in communicati | PASS |

## Chat Rooms
- **A: Basic Conversation**: `2dddafc7-1656-4f24-8311-d4b8e3049f6c`
- **B: Agent Rehydration**: `2dddafc7-1656-4f24-8311-d4b8e3049f6c`
- **C: Context Isolation**: `2dddafc7-1656-4f24-8311-d4b8e3049f6c`

## Startup Logs (excerpt)
```
[stderr] 2026-06-10 19:09:03 [INFO] band.adapters.letta: Found existing MCP server 'band' (id=mcp_server-2cd755b7-bcd0-40b6-9148-8c6fbee9e567)
[stderr] 2026-06-10 19:09:03 [INFO] band.adapters.letta: Discovered 22 MCP tools: ['add_agent_chat_participant', 'add_agent_contact', 'create_agent_chat', 'create_agent_chat_event', 'create_agent_chat_message', 'get_agent_chat', 'get_agent_chat_context', 'get_agent_me', 'get_agent_next_message', 'health_check', 'list_agent_chat_participants', 'list_agent_chats', 'list_agent_contact_requests', 'list_agent_contacts', 'list_agent_messages', 'list_agent_peers', 'mark_agent_message_failed', 'mark_agent_message_processed', 'mark_agent_message_processing', 'remove_agent_chat_participant', 'remove_agent_contact', 'respond_to_agent_contact_request']
[stderr] 2026-06-10 19:09:03 [INFO] band.adapters.letta: Letta adapter started for agent: QA-Letta-letta_agent-1921 (mode=per_room)
[stderr] 2026-06-10 19:09:03 [INFO] band.runtime.runtime: Starting AgentRuntime for agent 5a0f8640-d7a9-4a2c-900f-8807404462f7
[stderr] 2026-06-10 19:09:03 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_control:5a0f8640-d7a9-4a2c-900f-8807404462f7
[stderr] 2026-06-10 19:09:03 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_control:5a0f8640-d7a9-4a2c-900f-8807404462f7
[stderr] 2026-06-10 19:09:03 [INFO] band.platform.link: Connected to platform
[stderr] 2026-06-10 19:09:03 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:5a0f8640-d7a9-4a2c-900f-8807404462f7
```
