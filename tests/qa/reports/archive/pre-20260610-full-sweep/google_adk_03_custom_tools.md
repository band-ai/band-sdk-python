# QA Report: google_adk / 03_custom_tools

## Summary
- **Status:** PARTIAL
- **Date:** 2026-05-26
- **Platform:** app.band.ai
- **LLM:** gemini-2.5-flash
- **Agent ID:** 0ad71f38-787e-4645-bd0a-56400d2fe174
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PARTIAL
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Hello! I am QA-ADK-google_adk_agent-1917. I can help you w | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I'm sorry, I can't answer general knowledge questions like | FAIL |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6009a6e8-dd65-4b22-9309-e833dff76ace]] What is the second-largest city in France? | PASS |
| 4 | Send: List participants | Agent uses thenvoi_get_participants or describes participants | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I've forwarded your question about the second-largest city | PASS |
| 5 | Send: Lookup peers | Agent uses thenvoi_lookup_peers or responds about peers | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The participants in this chat room are: - QA-ADK-google_ad | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Here are the agents and users available on the platform:   | PASS |

### B: Agent Rehydration
**Status:** PARTIAL
*Kill and restart the agent, verify it re-joins and responds in the same room*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] You're welcome! Goodbye, and feel free to reach out if you | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 1 new message (pending-message reply only) | 1 new message(s), 0 unsolicited | PASS |
| 5 | Post-restart recall (message sent while down) | Agent recalls 'chrysanthemum' from transcript history | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Understood! I will remember the word 'chrysanthemum'. | PASS |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation | NO RESPONSE | FAIL |
| 7 | No extra responses after recall | 0 extra messages | No extra responses | PASS |

### C: Context Isolation
**Status:** PARTIAL
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=a37418df-3118-4a0b-a79f-f4154b8bfb8a | PASS |
| 2 | Add agent to new room | Agent added as participant | Added 0ad71f38-787e-4645-bd0a-56400d2fe174 | PASS |
| 3 | Ask about previous conversation | Agent responds with no prior context | NO RESPONSE | FAIL |
| 4 | Normal question in new room | Agent answers | NO RESPONSE | FAIL |

## Startup Logs (excerpt)
```
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/.venv/lib/python3.12/site-packages/authlib/_joserfc_helpers.py:8: AuthlibDeprecationWarning: authlib.jose module is deprecated, please use joserfc instead.
[stderr] It will be compatible before version 2.0.0.
[stderr]   from authlib.jose import ECKey
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/.venv/lib/python3.12/site-packages/google/cloud/aiplatform/models.py:52: FutureWarning: Support for google-cloud-storage < 3.0.0 will be removed in a future version of google-cloud-aiplatform. Please upgrade to google-cloud-storage >= 3.0.0.
[stderr]   from google.cloud.aiplatform.utils import gcs_utils
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/.venv/lib/python3.12/site-packages/pydantic/_internal/_fields.py:198: UserWarning: Field name "config_type" in "SequentialAgent" shadows an attribute in parent "BaseAgent"
[stderr]   warnings.warn(
[stderr] 2026-05-26 15:45:11 [INFO] thenvoi.adapters.google_adk: Google ADK adapter started for agent: QA-ADK-google_adk_agent-1917
[stderr] 2026-05-26 15:45:11 [INFO] thenvoi.runtime.runtime: Starting AgentRuntime for agent 0ad71f38-787e-4645-bd0a-56400d2fe174
[stderr] 2026-05-26 15:45:11 [INFO] thenvoi.platform.link: Connected to platform
[stderr] 2026-05-26 15:45:11 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:0ad71f38-787e-4645-bd0a-56400d2fe174
[stderr] 2026-05-26 15:45:11 [INFO] thenvoi.client.streaming.client: [WebSocket] Subscribed to topic: agent_rooms:0ad71f38-787e-4645-bd0a-56400d2fe174
```
