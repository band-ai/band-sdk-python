# QA Report: anthropic / 01_basic_agent

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** claude-sonnet-4-5-20250929
- **Agent ID:** 5b83a34e-4d41-446e-a491-b6391e030161
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PASS
**Room:** `a21240f1-f8d6-4ddd-a29a-5a76401c9730`
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | Hello @[[6d8e9293-5939-45b9-9de9-8742bafd896d]]! 👋  I'm QA-Anth-anthropic_agent-1911, a helpful assi | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The capital of France is **Paris**! 🇫🇷  Paris is not only  | PASS |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The second-largest city in France is **Marseille**! 🌊  Mar | PASS |
| 4 | Send: List participants | Agent uses band_get_participants or describes participants | Here are the participants currently in this chat room:  👤 **Nir Singher Test** (@[[6d8e9293-5939-45b | PASS |
| 5 | Send: Lookup peers | Agent uses band_lookup_peers or responds about peers | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I found quite a few agents available on the platform! Ther | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | You're very welcome, @[[6d8e9293-5939-45b9-9de9-8742bafd896d]]! It was a pleasure helping you today. | PASS |

### B: Agent Rehydration
**Status:** PARTIAL
**Room:** `a21240f1-f8d6-4ddd-a29a-5a76401c9730`
*Kill and restart the agent, verify it re-joins and responds in the same room*

> Error: Unsolicited messages after restart: 3 extra message(s) replayed from rehydrated history

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | Got it, @[[6d8e9293-5939-45b9-9de9-8742bafd896d]]! I'll remember the word **'platypus'**. 🦆  Just to | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 1 new message(s) (1 pending + 0 in-flight) | FAIL: 4 new message(s) — 3 unsolicited beyond 1 allowed | FAIL |
| 5 | Post-restart recall (message sent while down) | Agent recalls 'platypus' from transcript history | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The word you asked me to remember earlier is **'platypus'* | PASS |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation including 'platypus' | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Based on our conversation history, here's a summary of eve | PASS |
| 7 | No extra responses after recall | 0 extra messages | No extra responses | PASS |

### C: Context Isolation
**Status:** PASS
**Room:** `a21240f1-f8d6-4ddd-a29a-5a76401c9730`
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=25425381-c48a-4d9f-9765-1ac7dd379c97 | PASS |
| 2 | Add agent to new room | Agent added as participant | Added 5b83a34e-4d41-446e-a491-b6391e030161 | PASS |
| 3 | Ask about previous conversation | No content from other rooms | Hi @[[6d8e9293-5939-45b9-9de9-8742bafd896d]]! This is actually the start of our conversation - we ha | PASS |
| 4 | Normal question in new room | Agent answers correctly (4) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] 2 + 2 = 4 | PASS |

## Chat Rooms
- **A: Basic Conversation**: `a21240f1-f8d6-4ddd-a29a-5a76401c9730`
- **B: Agent Rehydration**: `a21240f1-f8d6-4ddd-a29a-5a76401c9730`
- **C: Context Isolation**: `a21240f1-f8d6-4ddd-a29a-5a76401c9730`

## Startup Logs (excerpt)
```
[stderr] 2026-06-03 22:02:18 [INFO] band.adapters.anthropic: Anthropic adapter started for agent: QA-Anth-anthropic_agent-1911
[stderr] 2026-06-03 22:02:18 [INFO] band.runtime.runtime: Starting AgentRuntime for agent 5b83a34e-4d41-446e-a491-b6391e030161
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_control:5b83a34e-4d41-446e-a491-b6391e030161
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_control:5b83a34e-4d41-446e-a491-b6391e030161
[stderr] 2026-06-03 22:02:19 [INFO] band.platform.link: Connected to platform
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:5b83a34e-4d41-446e-a491-b6391e030161
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_rooms:5b83a34e-4d41-446e-a491-b6391e030161
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:a919cfda-bb38-4267-bf9e-4aa727751f6d
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:412d26e3-8179-489d-bd6b-d662eda6866c
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:b775c6f9-867d-47c8-ab07-153e88b8c0d7
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:1a3e706c-b38e-4259-88a4-8447c111f9f5
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:b778e958-15b5-40f9-81db-0c2fabf0fc46
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:1ef2a637-264d-4f78-8c7c-b702fbc6f09e
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:724405ed-da3e-4c52-a814-8ec99f3435ef
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:d4f0b651-be19-4a14-8a69-064728f16b4f
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:4c7de139-1041-45e7-84d1-e23352d005bc
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:f6c4aaf2-ba25-414e-a0e4-bcb9653d1164
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:5eec3c98-743d-445d-bc1d-2e52de54abd7
```
