# QA Report: google_adk / 03_custom_tools

## Summary
- **Status:** PASS
- **Date:** 2026-05-26
- **Platform:** app.band.ai
- **LLM:** gemini-2.5-flash
- **Agent ID:** d2bb53ae-b60e-4539-8b9d-92b5ae73ed6d
- **Startup:** OK

## Scenario Results

### B: Agent Rehydration
**Status:** PASS
*Build up conversation history with a trivia question, then test two restart variants: (1) clean restart — agent must stay silent, then recall the trivia; (2) restart with pending message — agent must respond exactly once, then recall the trivia.*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Build conversation history (includes trivia question) | 3 exchanges | 16 messages in room | PASS |
| 2 | [Clean] Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | [Clean] Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | [Clean] No spontaneous messages after restart | 0 new agent text messages | 0 new text messages — agent stayed silent | PASS |
| 5 | [Clean] Recall trivia from before restart | Agent remembers Uganda/president question | @[[00000000-0000-0000-0000-000000000001]] Earlier, you asked me "Who was the president of Uganda in  | PASS |
| 6 | [Pending] Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 7 | [Pending] Restart agent | Agent starts and reconnects | started=True | PASS |
| 8 | [Pending] Exactly one response to pending message | 1 new agent text message | 1 text message (113 chars) | PASS |
| 9 | [Pending] Recall trivia after second restart | Agent remembers Uganda/president question | @[[00000000-0000-0000-0000-000000000001]] Earlier, you asked me "Who was the president of Uganda in  | PASS |
| 10 | [Pending] No trailing messages after settling | 0 extra messages | No trailing messages | PASS |

## Startup Logs (excerpt)
```
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/fix-int-509-adk-converter-includes-own-replies/.venv/lib/python3.12/site-packages/authlib/_joserfc_helpers.py:8: AuthlibDeprecationWarning: authlib.jose module is deprecated, please use joserfc instead.
[stderr] It will be compatible before version 2.0.0.
[stderr]   from authlib.jose import ECKey
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/fix-int-509-adk-converter-includes-own-replies/.venv/lib/python3.12/site-packages/google/cloud/aiplatform/models.py:52: FutureWarning: Support for google-cloud-storage < 3.0.0 will be removed in a future version of google-cloud-aiplatform. Please upgrade to google-cloud-storage >= 3.0.0.
[stderr]   from google.cloud.aiplatform.utils import gcs_utils
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/fix-int-509-adk-converter-includes-own-replies/.venv/lib/python3.12/site-packages/pydantic/_internal/_fields.py:198: UserWarning: Field name "config_type" in "SequentialAgent" shadows an attribute in parent "BaseAgent"
[stderr]   warnings.warn(
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.adapters.google_adk: Google ADK adapter started for agent: ADK-QA-Full
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.runtime.runtime: Starting AgentRuntime for agent d2bb53ae-b60e-4539-8b9d-92b5ae73ed6d
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.platform.link: Connected to platform
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:d2bb53ae-b60e-4539-8b9d-92b5ae73ed6d
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribed to topic: agent_rooms:d2bb53ae-b60e-4539-8b9d-92b5ae73ed6d
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:e6fb9492-2e3a-4ee2-a8d8-fc779c38ecc4
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:c7800fa9-49e7-4110-b7f2-eb1ff0bc56be
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:26f31940-54f9-4887-851b-28d170172c20
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:7534cad1-30f4-4b8b-bc23-b50fb3cbfda0
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:c350981a-33c4-4d01-be6f-8eb0a85f5ed1
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:7b7f44c2-9a4a-4e77-a786-c9e561e0cec1
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:3a3aaff4-9089-404c-8efb-6763505535ac
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:256c5631-40a8-454d-9690-512127951d87
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:5469f5f9-fd56-41f8-a867-d07605b43b08
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:3d75b169-efc6-48fa-8533-1dba47832fe2
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:1e59b4aa-a76f-467b-b57e-d4f525741d92
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:0b37716d-9e58-47f4-8983-25c41a3f701c
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:9dc670f1-13de-4627-b618-d8953de5b836
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:0828962b-06fd-475d-9338-89832ff3b2fe
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:026ba635-5214-48b3-a3ae-1f1bd304215e
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:56b86b91-3d20-4e39-92b0-4fb6e4630f00
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:5c7835f1-e7ea-475c-bb32-0e37cff32190
[stderr] 2026-05-26 10:38:56 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: chat_room:1e7634e9-b947-459a-b366-d78da310a7e9
... (49 more lines)
```
