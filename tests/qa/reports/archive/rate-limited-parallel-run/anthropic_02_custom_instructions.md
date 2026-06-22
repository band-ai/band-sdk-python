# QA Report: anthropic / 02_custom_instructions

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-10
- **Platform:** app.band.ai
- **LLM:** claude-sonnet-4-5-20250929
- **Agent ID:** 5b83a34e-4d41-446e-a491-b6391e030161
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PASS
**Room:** `bc16d67c-ea19-46db-a82c-a474dd83efa3`
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | Hello @[[6d8e9293-5939-45b9-9de9-8742bafd896d]]! 👋  I'm QA-Anth-anthropic_agent-1911, a technical su | PASS |
| 2 | Send: Domain question | Agent answers Paris | Hello @[[6d8e9293-5939-45b9-9de9-8742bafd896d]]!   The capital of France is **Paris**. 🇫🇷  However,  | PASS |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The second-largest city in France is **Marseille**, locate | PASS |
| 4 | Send: List participants | Agent uses band_get_participants or describes participants | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Sure! Here are the current participants in this chat room: | PASS |
| 5 | Send: Lookup peers | Agent uses band_lookup_peers or responds about peers | {"name": "band_send_event", "output": "{\"id\": \"4fb67e6e-c251-4105-9d8a-45048568e55f\", \"message_ | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | {"name": "band_lookup_peers", "output": "{\"data\": [{\"description\": \"Shared QA test agent for Go | PASS |

### B: Agent Rehydration
**Status:** PARTIAL
**Room:** `bc16d67c-ea19-46db-a82c-a474dd83efa3`
*Kill and restart the agent, verify it re-joins and responds in the same room*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Sure! I've looked up the available peers on the platform.  | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 3 new message(s) (1 pending + 2 in-flight) | 1 new message(s), 0 unsolicited | PASS |
| 5 | Post-restart recall (message sent while down) | Agent recalls 'chrysanthemum' from transcript history | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The word you asked me to remember earlier was **"chrysanth | PASS |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation including 'chrysanthemum' | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] You're welcome! It was great helping you today. Feel free  | PARTIAL |
| 7 | No extra responses after recall | 0 extra text messages | FAIL: 2 extra text response(s) | FAIL |

### C: Context Isolation
**Status:** PASS
**Room:** `bc16d67c-ea19-46db-a82c-a474dd83efa3`
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=52b438b1-d104-450b-802b-0718c4fff7bf | PASS |
| 2 | Add agent to new room | Agent added as participant | Added 5b83a34e-4d41-446e-a491-b6391e030161 | PASS |
| 3 | Ask about previous conversation | No content from other rooms | Hello @[[6d8e9293-5939-45b9-9de9-8742bafd896d]]!   I don't have any record of previous discussions b | PASS |
| 4 | Normal question in new room | Agent answers correctly (4) | Hello @[[6d8e9293-5939-45b9-9de9-8742bafd896d]]!  2 + 2 = 4  Is there anything else I can help you w | PASS |

## Chat Rooms
- **A: Basic Conversation**: `bc16d67c-ea19-46db-a82c-a474dd83efa3`
- **B: Agent Rehydration**: `bc16d67c-ea19-46db-a82c-a474dd83efa3`
- **C: Context Isolation**: `bc16d67c-ea19-46db-a82c-a474dd83efa3`

## Startup Logs (excerpt)
```
[stderr] 2026-06-10 15:20:55 [INFO] band.adapters.anthropic: Anthropic adapter started for agent: QA-Anth-anthropic_agent-1911
[stderr] 2026-06-10 15:20:55 [INFO] band.runtime.runtime: Starting AgentRuntime for agent 5b83a34e-4d41-446e-a491-b6391e030161
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_control:5b83a34e-4d41-446e-a491-b6391e030161
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_control:5b83a34e-4d41-446e-a491-b6391e030161
[stderr] 2026-06-10 15:20:56 [INFO] band.platform.link: Connected to platform
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:5b83a34e-4d41-446e-a491-b6391e030161
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_rooms:5b83a34e-4d41-446e-a491-b6391e030161
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:3b6a40cd-5f31-436e-b68f-d5f19e7b8290
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:7dae9d2b-45d9-4a0d-a737-3c2d65bb6c41
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:5be1b930-ba2d-4897-98b1-ca341b661da1
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:dfceee27-785c-46f3-8901-d1e0fa6f2f3a
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:9a9e44ad-346a-4a9c-aece-9d05bd538d12
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:ed87bda3-d45a-4ba2-ac13-047948ebd399
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:88e98b63-7db2-4f5a-ab6f-e92a349c7308
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:80672959-9f06-4433-ac1e-4f09936072ab
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:6dcfce45-9974-4155-9ae4-e007486e8f8b
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:b064b777-d247-40dc-8389-7a78f68e20fe
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:25425381-c48a-4d9f-9765-1ac7dd379c97
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:a21240f1-f8d6-4ddd-a29a-5a76401c9730
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:a919cfda-bb38-4267-bf9e-4aa727751f6d
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:412d26e3-8179-489d-bd6b-d662eda6866c
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:b775c6f9-867d-47c8-ab07-153e88b8c0d7
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:1a3e706c-b38e-4259-88a4-8447c111f9f5
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:b778e958-15b5-40f9-81db-0c2fabf0fc46
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:1ef2a637-264d-4f78-8c7c-b702fbc6f09e
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:724405ed-da3e-4c52-a814-8ec99f3435ef
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:d4f0b651-be19-4a14-8a69-064728f16b4f
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:4c7de139-1041-45e7-84d1-e23352d005bc
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:f6c4aaf2-ba25-414e-a0e4-bcb9653d1164
[stderr] 2026-06-10 15:20:56 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:5eec3c98-743d-445d-bc1d-2e52de54abd7
```
