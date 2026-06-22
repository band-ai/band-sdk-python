# QA Report: anthropic / 01_basic_agent

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
**Room:** `7dae9d2b-45d9-4a0d-a737-3c2d65bb6c41`
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Hello! 👋 I'm QA-Anth-anthropic_agent-1911, a helpful assis | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The capital of France is **Paris**! 🇫🇷  Paris is not only  | PASS |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The second-largest city in France is **Marseille**! 🌊  Mar | PASS |
| 4 | Send: List participants | Agent uses band_get_participants or describes participants | Here are the current participants in this chat room:  👤 **Nir Singher Test** (@[[6d8e9293-5939-45b9- | PASS |
| 5 | Send: Lookup peers | Agent uses band_lookup_peers or responds about peers | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Sure! I found **184 available peers** on the platform (sho | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] You're welcome! It was great chatting with you. Goodbye, a | PASS |

### B: Agent Rehydration
**Status:** PARTIAL
**Room:** `7dae9d2b-45d9-4a0d-a737-3c2d65bb6c41`
*Kill and restart the agent, verify it re-joins and responds in the same room*

> Error: Unsolicited messages after restart: 3 extra message(s) replayed from rehydrated history

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Got it! I'll remember the word **'chrysanthemum'**. 🌼   Ju | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 1 new message(s) (1 pending + 0 in-flight) | FAIL: 4 new message(s) — 3 unsolicited beyond 1 allowed | FAIL |
| 5 | Post-restart recall (message sent while down) | Agent recalls 'chrysanthemum' from transcript history | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The word you asked me to remember earlier was: **chrysanth | PASS |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation including 'chrysanthemum' | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Based on our conversation history before the restart, here | PASS |
| 7 | No extra responses after recall | 0 extra messages | No extra responses | PASS |

### C: Context Isolation
**Status:** PASS
**Room:** `7dae9d2b-45d9-4a0d-a737-3c2d65bb6c41`
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=3b6a40cd-5f31-436e-b68f-d5f19e7b8290 | PASS |
| 2 | Add agent to new room | Agent added as participant | Added 5b83a34e-4d41-446e-a491-b6391e030161 | PASS |
| 3 | Ask about previous conversation | No content from other rooms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Hi! This is actually the first message in our conversation | PASS |
| 4 | Normal question in new room | Agent answers correctly (4) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] 2 + 2 = 4 | PASS |

## Chat Rooms
- **A: Basic Conversation**: `7dae9d2b-45d9-4a0d-a737-3c2d65bb6c41`
- **B: Agent Rehydration**: `7dae9d2b-45d9-4a0d-a737-3c2d65bb6c41`
- **C: Context Isolation**: `7dae9d2b-45d9-4a0d-a737-3c2d65bb6c41`

## Startup Logs (excerpt)
```
[stderr] 2026-06-10 15:16:33 [INFO] band.adapters.anthropic: Anthropic adapter started for agent: QA-Anth-anthropic_agent-1911
[stderr] 2026-06-10 15:16:33 [INFO] band.runtime.runtime: Starting AgentRuntime for agent 5b83a34e-4d41-446e-a491-b6391e030161
[stderr] 2026-06-10 15:16:33 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_control:5b83a34e-4d41-446e-a491-b6391e030161
[stderr] 2026-06-10 15:16:34 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_control:5b83a34e-4d41-446e-a491-b6391e030161
[stderr] 2026-06-10 15:16:34 [INFO] band.platform.link: Connected to platform
[stderr] 2026-06-10 15:16:34 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:5b83a34e-4d41-446e-a491-b6391e030161
```
