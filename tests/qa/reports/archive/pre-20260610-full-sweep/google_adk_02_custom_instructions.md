# QA Report: google_adk / 02_custom_instructions

## Summary
- **Status:** PARTIAL
- **Date:** 2026-05-26
- **Platform:** app.band.ai
- **LLM:** gemini-2.5-flash
- **Agent ID:** 0ad71f38-787e-4645-bd0a-56400d2fe174
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PASS
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Hello! I am a research assistant specializing in summarizi | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The capital of France is Paris. | PASS |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The second-largest city in France is Marseille. | PASS |
| 4 | Send: List participants | Agent uses thenvoi_get_participants or describes participants | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I have answered your question. If you have any more questi | PASS |
| 5 | Send: Lookup peers | Agent uses thenvoi_lookup_peers or responds about peers | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Of course! The participants in this chatroom are: "QA-ADK- | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I have answered your question. If you have any more questi | PASS |

### B: Agent Rehydration
**Status:** FAIL
*Kill and restart the agent, verify it re-joins and responds in the same room*

> Error: Extra unsolicited responses after recall: 1 text message(s) sent without user prompt

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | {"name": "thenvoi_lookup_peers", "output": "{'result': '{\"data\": [{\"description\": \"Shared QA te | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 1 new message (pending-message reply only) | FAIL: 3 new message(s) — 3 unsolicited beyond pending reply | FAIL |
| 5 | Post-restart recall (message sent while down) | Agent recalls 'zeppelin' from transcript history | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] You asked me to remember the word 'zeppelin'. | PASS |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation including 'zeppelin' | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I am ready for my next question. | PARTIAL |
| 7 | No extra responses after recall | 0 extra text messages | FAIL: 1 extra text response(s) | FAIL |

### C: Context Isolation
**Status:** PASS
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=6c96046c-25c1-46e2-9371-7ff96a416457 | PASS |
| 2 | Add agent to new room | Agent added as participant | Added 0ad71f38-787e-4645-bd0a-56400d2fe174 | PASS |
| 3 | Ask about previous conversation | No content from other rooms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] So far, we have not discussed anything. This is the first  | PASS |
| 4 | Normal question in new room | Agent answers correctly (4) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] 2 + 2 = 4 | PASS |

## Startup Logs (excerpt)
```
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/.venv/lib/python3.12/site-packages/authlib/_joserfc_helpers.py:8: AuthlibDeprecationWarning: authlib.jose module is deprecated, please use joserfc instead.
[stderr] It will be compatible before version 2.0.0.
[stderr]   from authlib.jose import ECKey
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/.venv/lib/python3.12/site-packages/google/cloud/aiplatform/models.py:52: FutureWarning: Support for google-cloud-storage < 3.0.0 will be removed in a future version of google-cloud-aiplatform. Please upgrade to google-cloud-storage >= 3.0.0.
[stderr]   from google.cloud.aiplatform.utils import gcs_utils
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/.venv/lib/python3.12/site-packages/pydantic/_internal/_fields.py:198: UserWarning: Field name "config_type" in "SequentialAgent" shadows an attribute in parent "BaseAgent"
[stderr]   warnings.warn(
[stderr] 2026-05-26 15:41:25 [INFO] thenvoi.adapters.google_adk: Google ADK adapter started for agent: QA-ADK-google_adk_agent-1917
[stderr] 2026-05-26 15:41:25 [INFO] thenvoi.runtime.runtime: Starting AgentRuntime for agent 0ad71f38-787e-4645-bd0a-56400d2fe174
[stderr] 2026-05-26 15:41:25 [INFO] thenvoi.platform.link: Connected to platform
[stderr] 2026-05-26 15:41:25 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:0ad71f38-787e-4645-bd0a-56400d2fe174
[stderr] 2026-05-26 15:41:25 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribed to topic: agent_rooms:0ad71f38-787e-4645-bd0a-56400d2fe174
[stderr] 2026-05-26 15:41:26 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:15a1af7a-c968-43bf-b781-b3938bce3760
[stderr] 2026-05-26 15:41:26 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:f94ff2f5-03ea-4bab-86aa-e3dc1be93b21
[stderr] 2026-05-26 15:41:26 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:5c10409e-edc9-4303-b4c5-54ba41a9a741
[stderr] 2026-05-26 15:41:26 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:97808303-2686-4046-b4cc-5585a2e929c5
[stderr] 2026-05-26 15:41:26 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:f9bf58ef-1704-4ecd-94c7-a37d7f2ead30
[stderr] 2026-05-26 15:41:26 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:c1ed47db-26d7-4fde-ad2a-18ce3a79a1bd
[stderr] 2026-05-26 15:41:26 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:2298d013-2c27-42a1-a35c-bdd0a21699a5
```
