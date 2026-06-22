# QA Report: pydantic_ai / 01_basic_agent

## Summary
- **Status:** PARTIAL
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** openai:gpt-5.4-mini
- **Agent ID:** c1172de7-dc42-496c-9bbb-a24e2f3f5594
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PASS
**Room:** `bcbce91d-2e40-44df-8364-a6adc2c5ce05`
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | Hello @[[6d8e9293-5939-45b9-9de9-8742bafd896d]]! I’m an AI assistant in this chat, here to help with | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The capital of France is Paris. | PASS |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I’m missing the earlier location context here. If you tell | PASS |
| 4 | Send: List participants | Agent uses band_get_participants or describes participants | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Here’s who is in this chat room: - @[[6d8e9293-5939-45b9-9 | PASS |
| 5 | Send: Lookup peers | Agent uses band_lookup_peers or responds about peers | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Yes — there are many available peers on the platform. I fo | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] You’re welcome — goodbye! | PASS |

### B: Agent Rehydration
**Status:** PARTIAL
**Room:** `bcbce91d-2e40-44df-8364-a6adc2c5ce05`
*Kill and restart the agent, verify it re-joins and responds in the same room*

> Error: Unsolicited messages after restart: 4 extra message(s) replayed from rehydrated history

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Got it — I’ll remember the word “xylophone” for when you a | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 1 new message(s) (1 pending + 0 in-flight) | FAIL: 5 new message(s) — 4 unsolicited beyond 1 allowed | FAIL |
| 5 | Post-restart recall (message sent while down) | Agent recalls 'xylophone' from transcript history | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] You asked me to remember: xylophone. | PASS |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation including 'xylophone' | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Before the restart, we discussed: - I introduced myself as | PASS |
| 7 | No extra responses after recall | 0 extra messages | No extra responses | PASS |

### C: Context Isolation
**Status:** PASS
**Room:** `bcbce91d-2e40-44df-8364-a6adc2c5ce05`
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=2a92c8b7-f94a-4d69-a357-30c69ef79a62 | PASS |
| 2 | Add agent to new room | Agent added as participant | Added c1172de7-dc42-496c-9bbb-a24e2f3f5594 | PASS |
| 3 | Ask about previous conversation | No content from other rooms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] I only have visibility into the current message thread, an | PASS |
| 4 | Normal question in new room | Agent answers correctly (4) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] 2 + 2 = 4. | PASS |

## Chat Rooms
- **A: Basic Conversation**: `bcbce91d-2e40-44df-8364-a6adc2c5ce05`
- **B: Agent Rehydration**: `bcbce91d-2e40-44df-8364-a6adc2c5ce05`
- **C: Context Isolation**: `bcbce91d-2e40-44df-8364-a6adc2c5ce05`

## Startup Logs (excerpt)
```
[stderr] /Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/.venv-int488/lib/python3.12/site-packages/pydantic_ai/agent/__init__.py:410: PydanticAIDeprecationWarning: In v2.0, 'openai:' will resolve to the OpenAI Responses API by default. Use 'openai-chat:' to keep current Chat Completions behavior, or 'openai-responses:' to opt in early.
[stderr]   self._model = models.infer_model(model)
[stderr] 2026-06-03 22:02:19 [INFO] band.adapters.pydantic_ai: Pydantic AI adapter started for agent: QA-PAI-pydantic_agent-1921
[stderr] 2026-06-03 22:02:19 [INFO] band.runtime.runtime: Starting AgentRuntime for agent c1172de7-dc42-496c-9bbb-a24e2f3f5594
[stderr] 2026-06-03 22:02:19 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_control:c1172de7-dc42-496c-9bbb-a24e2f3f5594
[stderr] 2026-06-03 22:02:20 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_control:c1172de7-dc42-496c-9bbb-a24e2f3f5594
[stderr] 2026-06-03 22:02:20 [INFO] band.platform.link: Connected to platform
[stderr] 2026-06-03 22:02:20 [INFO] band.client.streaming.client: [WebSocket] Subscribing to topic: agent_rooms:c1172de7-dc42-496c-9bbb-a24e2f3f5594
[stderr] 2026-06-03 22:02:20 [INFO] band.client.streaming.client: [WebSocket] Subscribed to topic: agent_rooms:c1172de7-dc42-496c-9bbb-a24e2f3f5594
```
