# Scenarios — What and How to Test

## Scenario Table

| ID | Name | What It Tests | Pass Criteria |
|----|------|---------------|---------------|
| A | Basic Conversation | 6-message flow: greeting, domain Q&A, context retention, platform tools (get_participants, lookup_peers), farewell | Agent responds coherently to all 6 messages |
| B | Agent Rehydration | SIGINT → restart → same room | Agent re-joins, recalls pre-restart context ("pineapple"), no duplicate/replayed messages |
| C | Context Isolation | New room, same agent | No context leak from previous room |
| D | Multi-Participant | Two agents in one room (cross-adapter: langgraph + letta) | Both agents respond when mentioned |
| E | Memory Tools | store → list → get → cross-room recall → supersede → store updated → archive → final list | Full memory lifecycle works |
| F1 | Contact DISABLED | Send contact request to DISABLED agent | Request stays pending; no contact messages in room |
| F2 | Contact CALLBACK | Send whitelisted contact request | Auto-approved; broadcast notification; no LLM invocation |
| F3 | Contact HUB_ROOM | 3 variants: friendly, spam, empty | LLM approves friendly, rejects spam, handles empty |
| G | Execution Emit | Trigger tool use (get_participants, lookup_peers) | tool_call + tool_result events visible on platform |
| I | Concurrent Rooms | 3 rooms with secrets ALPHA/BRAVO/CHARLIE | Each room recalls its own secret only |

### Core vs Expanded

- **Core (A-C):** Run per example file. Validates that any example works end-to-end.
- **Scenario D:** Cross-adapter. Uses the `multi_participant` config from the adapter's `config.yaml`.
- **Expanded (E-I):** Run with purpose-built agent scripts that enable specific features. Validates SDK capabilities.

## What to Watch For

### Agent Startup

| Check | Timeout | Signal |
|-------|---------|--------|
| Process starts | 180s | `Agent started:` or `[WebSocket] Subscribed to topic:` in logs |
| No errors | — | No import errors, missing env vars, or dependency crashes |
| No warnings | — | Unexpected warnings may indicate config issues |

Latency is not important for startup — some adapters (especially letta) can take over a minute. The 180s timeout covers edge cases.

### Agent Responds to Messages

| Check | Timeout | Details |
|-------|---------|---------|
| Takes action | 120s | Any activity: tool_call, tool_result, or reasoning visible in message stream |
| Sends text reply | 120s | A `direct_message` (message_type=text) back to the chatroom |
| Response is coherent | — | Content is relevant to the question asked |

All LLM-involving timeouts are 120s+. If an agent takes action (tool calls) but never sends a text message, that's a PARTIAL — not a PASS.

### Rehydration (Scenario B) — Critical

This is the highest-priority area. Known failure modes:

| Check | What to look for |
|-------|-----------------|
| No replayed messages | After restart, the agent must NOT respond to messages it already handled before the kill. Count text messages from the agent after restart — any unsolicited ones are a FAIL. |
| Responds to pending messages | A message sent while the agent was down should get exactly one response after restart. |
| Recalls conversation history | The restarted agent can reference pre-restart context (the word "pineapple") from the chatroom transcript. |
| No duplicate processing | The agent should not re-process old events. Watch for doubled tool_call/tool_result pairs. |
| Graceful shutdown | SIGINT should trigger `graceful=True` in logs. If not, the agent may not have persisted state. |

### Context Isolation (Scenario C)

- New room = clean slate. The agent must NOT know about "pineapple" from a previous room.
- Should answer new questions correctly without referencing prior rooms.

### Platform Tools (Scenarios A, G)

- `band_get_participants`: agent uses it when asked to list participants.
- `band_lookup_peers`: agent uses it when asked about available agents.
- Both tool_call and tool_result events should appear in the message stream.

### Multi-Participant (Scenario D)

- Both agents (langgraph + letta) are added to the same room.
- A single message mentioning both agents should trigger responses from each.
- Timeout: 120s per agent. Both must respond.

### Memory (Scenario E)

- `band_store_memory` returns a memory ID.
- `band_list_memories` shows stored content. Known issue: PLT-915 may return empty without explicit `subject_id`.
- Memory persists across rooms.

### Contacts (Scenarios F1-F3)

- DISABLED: request stays pending, no room activity.
- CALLBACK: auto-approve fires within 15s.
- HUB_ROOM: LLM processes within 120s (timing-sensitive).
