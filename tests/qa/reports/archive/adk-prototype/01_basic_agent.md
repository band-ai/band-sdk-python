# QA Report: google_adk / 01_basic_agent

## Summary
- **Status:** PARTIAL
- **Date:** 2026-05-25
- **Platform:** app.band.ai
- **LLM:** gemini-2.5-flash
- **Agent ID:** d2bb53ae-b60e-4539-8b9d-92b5ae73ed6d
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PARTIAL
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | Hello @[[00000000-0000-0000-0000-000000000001]]! I'm ADK-QA-Full, an agent designed to help with var | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[00000000-0000-0000-0000-000000000001]] The capital of France is Paris. | PASS |
| 3 | Send: Follow-up (context) | Agent references France context | @[[00000000-0000-0000-0000-000000000001]] The second-largest city in France is Marseille. | PASS |
| 4 | Send: List participants | Agent uses thenvoi_get_participants or describes participants | The participants in this chat room are: FusionAuth Admin (@[[00000000-0000-0000-0000-000000000001]]) | PASS |
| 5 | Send: Lookup peers | Agent uses thenvoi_lookup_peers or responds about peers | NO RESPONSE (timeout) | FAIL |
| 6 | Send: Goodbye | Agent responds with farewell | You're welcome! Goodbye, @[[00000000-0000-0000-0000-000000000001]]! | PASS |

### B: Agent Rehydration
**Status:** PASS
*Kill and restart the agent, verify it re-joins and responds in the same room*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | @[[00000000-0000-0000-0000-000000000001]] I will remember the word 'pineapple' for you. | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | Post-restart recall | Agent recalls 'pineapple' from transcript history | @[[00000000-0000-0000-0000-000000000001]] You asked me to remember the word 'pineapple'. | PASS |
| 5 | Check for duplicate messages | No duplicate agent responses | No duplicates detected | PASS |

### C: Context Isolation
**Status:** PASS
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=0b37716d-9e58-47f4-8983-25c41a3f701c | PASS |
| 2 | Add agent to new room | Agent added as participant | Added d2bb53ae-b60e-4539-8b9d-92b5ae73ed6d | PASS |
| 3 | Ask about previous conversation | Agent has no memory of previous room | @[[00000000-0000-0000-0000-000000000001]] I'm sorry, I don't have access to past conversation histor | PASS |
| 4 | Normal question in new room | Agent answers correctly (4) | @[[00000000-0000-0000-0000-000000000001]] 2 + 2 = 4. | PASS |

## Warnings
- DeprecationWarning detected in logs

## Startup Logs (excerpt)
```
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/fix-int-509-adk-converter-includes-own-replies/.venv/lib/python3.12/site-packages/authlib/_joserfc_helpers.py:8: AuthlibDeprecationWarning: authlib.jose module is deprecated, please use joserfc instead.
[stderr] It will be compatible before version 2.0.0.
[stderr]   from authlib.jose import ECKey
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/fix-int-509-adk-converter-includes-own-replies/.venv/lib/python3.12/site-packages/google/cloud/aiplatform/models.py:52: FutureWarning: Support for google-cloud-storage < 3.0.0 will be removed in a future version of google-cloud-aiplatform. Please upgrade to google-cloud-storage >= 3.0.0.
[stderr]   from google.cloud.aiplatform.utils import gcs_utils
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/fix-int-509-adk-converter-includes-own-replies/.venv/lib/python3.12/site-packages/pydantic/_internal/_fields.py:198: UserWarning: Field name "config_type" in "SequentialAgent" shadows an attribute in parent "BaseAgent"
[stderr]   warnings.warn(
[stderr] 2026-05-25 15:38:31 [INFO] thenvoi.adapters.google_adk: Google ADK adapter started for agent: ADK-QA-Full
[stderr] 2026-05-25 15:38:31 [INFO] thenvoi.runtime.runtime: Starting AgentRuntime for agent d2bb53ae-b60e-4539-8b9d-92b5ae73ed6d
[stderr] 2026-05-25 15:38:31 [INFO] thenvoi.platform.link: Connected to platform
[stderr] 2026-05-25 15:38:31 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:d2bb53ae-b60e-4539-8b9d-92b5ae73ed6d
[stderr] 2026-05-25 15:38:31 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribed to topic: agent_rooms:d2bb53ae-b60e-4539-8b9d-92b5ae73ed6d
[stderr] 2026-05-25 15:38:31 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:1e7634e9-b947-459a-b366-d78da310a7e9
[stderr] 2026-05-25 15:38:31 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:7568ee07-ae48-4cd2-8d99-91565e8572e0
[stderr] 2026-05-25 15:38:31 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:b1879a6e-920c-42d2-a85e-b014ffb1ebcb
[stderr] 2026-05-25 15:38:31 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:56322170-f407-41bd-a9f9-946d4d085d64
[stderr] 2026-05-25 15:38:31 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: room_participants:1e7634e9-b947-459a-b366-d78da310a7e9
[stderr] 2026-05-25 15:38:31 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: room_participants:7568ee07-ae48-4cd2-8d99-91565e8572e0
```
