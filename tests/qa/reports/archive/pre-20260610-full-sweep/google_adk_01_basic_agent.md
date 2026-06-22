# QA Report: google_adk / 01_basic_agent

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** gemini-2.5-flash
- **Agent ID:** 0ad71f38-787e-4645-bd0a-56400d2fe174
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PARTIAL
**Room:** `c91c05fe-c7d4-4c64-9b45-0727d47b8627`
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | Hello @[[6d8e9293-5939-45b9-9de9-8742bafd896d]]! I'm QA-ADK-google_adk_agent-1917, a helpful assista | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[c1e8cc4a-f049-4c45-b385-e7f18d2921ea]] What is the capital of France? | FAIL |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The capital of France is Paris. | PASS |
| 4 | Send: List participants | Agent uses band_get_participants or describes participants | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The second-largest city in France is Marseille. | PASS |
| 5 | Send: Lookup peers | Agent uses band_lookup_peers or responds about peers | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The participants in this chat room are: Nir Singher Test ( | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Here are some of the other agents available on the platfor | PASS |

### B: Agent Rehydration
**Status:** PARTIAL
**Room:** `c91c05fe-c7d4-4c64-9b45-0727d47b8627`
*Kill and restart the agent, verify it re-joins and responds in the same room*

> Error: Unsolicited messages after restart: 1 extra message(s) replayed from rehydrated history

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | You're welcome! Goodbye, @[[6d8e9293-5939-45b9-9de9-8742bafd896d]]! | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 1 new message(s) (1 pending + 0 in-flight) | FAIL: 2 new message(s) — 1 unsolicited beyond 1 allowed | FAIL |
| 5 | Post-restart recall (message sent while down) | Agent recalls 'armadillo' from transcript history | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] You asked me to remember the word 'armadillo'. | PASS |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation including 'armadillo' | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Before the restart, we covered several topics:  1.  **My I | PARTIAL |
| 7 | No extra responses after recall | 0 extra messages | No extra responses | PASS |

### C: Context Isolation
**Status:** PASS
**Room:** `c91c05fe-c7d4-4c64-9b45-0727d47b8627`
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=304f4a56-52ec-4eac-8fda-b9c11d18a7e5 | PASS |
| 2 | Add agent to new room | Agent added as participant | Added 0ad71f38-787e-4645-bd0a-56400d2fe174 | PASS |
| 3 | Ask about previous conversation | No content from other rooms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] There's no prior discussion to summarize yet, as I've just | PASS |
| 4 | Normal question in new room | Agent answers correctly (4) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] 2 + 2 equals 4. | PASS |

## Chat Rooms
- **A: Basic Conversation**: `c91c05fe-c7d4-4c64-9b45-0727d47b8627`
- **B: Agent Rehydration**: `c91c05fe-c7d4-4c64-9b45-0727d47b8627`
- **C: Context Isolation**: `c91c05fe-c7d4-4c64-9b45-0727d47b8627`

## Startup Logs (excerpt)
```
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/.venv/lib/python3.12/site-packages/authlib/_joserfc_helpers.py:8: AuthlibDeprecationWarning: authlib.jose module is deprecated, please use joserfc instead.
[stderr] It will be compatible before version 2.0.0.
[stderr]   from authlib.jose import ECKey
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/.venv/lib/python3.12/site-packages/google/cloud/aiplatform/models.py:52: FutureWarning: Support for google-cloud-storage < 3.0.0 will be removed in a future version of google-cloud-aiplatform. Please upgrade to google-cloud-storage >= 3.0.0.
[stderr]   from google.cloud.aiplatform.utils import gcs_utils
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/.venv/lib/python3.12/site-packages/pydantic/_internal/_fields.py:198: UserWarning: Field name "config_type" in "SequentialAgent" shadows an attribute in parent "BaseAgent"
[stderr]   warnings.warn(
[stderr] 2026-06-03 23:47:53 [INFO] band.adapters.google_adk: Google ADK adapter started for agent: QA-ADK-google_adk_agent-1917
[stderr] 2026-06-03 23:47:53 [INFO] band.runtime.runtime: Starting AgentRuntime for agent 0ad71f38-787e-4645-bd0a-56400d2fe174
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_control:0ad71f38-787e-4645-bd0a-56400d2fe174
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_control:0ad71f38-787e-4645-bd0a-56400d2fe174
[stderr] 2026-06-03 23:47:54 [INFO] band.platform.link: Connected to platform
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:0ad71f38-787e-4645-bd0a-56400d2fe174
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_rooms:0ad71f38-787e-4645-bd0a-56400d2fe174
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:465fcff7-5c00-4d67-af27-9fa4360f4a16
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:fb368ee9-6cdb-40f0-88ef-b8b189a1b601
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:a37418df-3118-4a0b-a79f-f4154b8bfb8a
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:a2bfb9ff-5994-45bc-91a2-2c234a037158
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:51a3518b-3758-484a-82e5-0e7b666c3236
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:6c96046c-25c1-46e2-9371-7ff96a416457
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:15a1af7a-c968-43bf-b781-b3938bce3760
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:f94ff2f5-03ea-4bab-86aa-e3dc1be93b21
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:5c10409e-edc9-4303-b4c5-54ba41a9a741
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:97808303-2686-4046-b4cc-5585a2e929c5
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:f9bf58ef-1704-4ecd-94c7-a37d7f2ead30
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:c1ed47db-26d7-4fde-ad2a-18ce3a79a1bd
[stderr] 2026-06-03 23:47:54 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:2298d013-2c27-42a1-a35c-bdd0a21699a5
```
