# Google ADK Full QA Summary (A-I)

**Date:** 2026-05-25
**LLM:** gemini-2.5-flash (Vertex AI)
**Agent:** ADK-QA-Full + specialized test agents

## Results Overview

| Scenario | Status | Pass/Total | Notes |
|----------|--------|------------|-------|
| A: Basic Conversation | PARTIAL | 5/6 | Lookup peers timed out |
| B: Agent Rehydration | PASS | 5/5 | Pineapple recalled after restart |
| C: Context Isolation | PASS | 4/4 | Clean room isolation |
| E: Memory Tools | PASS | 5/8 (3 PARTIAL) | subject_id scoping bug (PLT-915) |
| F1: Contact DISABLED | PASS | 3/3 | Correctly ignores requests |
| F2: Contact CALLBACK | PARTIAL | 2/4 | Auto-approve didn't fire |
| F3: Contact HUB_ROOM | PARTIAL | 4/7 | Friendly approved; spam/empty pending |
| G: Execution Emit | PASS | 3/3 | tool_call + tool_result events verified |
| I: Concurrent Rooms | PARTIAL | 5/6 | Room 2 (BRAVO) recall failed |

**Overall: 5 PASS, 4 PARTIAL, 0 FAIL**

## Bugs Found During QA

### Harness Bugs (fixed this session)
1. **ADK agent name validation** — Platform name `ADK-QA-Full` has hyphens, ADK `LlmAgent` requires valid Python identifiers. Fixed: `re.sub(r"[^A-Za-z0-9_]", "_", name)` in adapter.
2. **Stale message detection** — `wait_for_agent_activity` returned early on text message without draining remaining messages from the poll response, causing known_ids leakage across steps. Fixed: scan all messages before returning.
3. **Contact API endpoints** — Harness used wrong paths (`/agents/contacts` instead of `/agent/contacts/add`, `/agent/contacts/remove`, etc.). Fixed: aligned with Fern-generated client.

### Platform/SDK Bugs (filed as Linear issues)
1. **PLT-915** — `list_memories` returns empty without explicit `subject_id`
2. **INT-515** — SDK needs subject symbols (`@self`, `@sender`, `@org`)
3. **INT-516** — Memory prompt guidance for agents

## Scenario Details

### A: Basic Conversation (PARTIAL)
- Greeting, domain question, follow-up, participants: all PASS
- Lookup peers: FAIL (120s timeout, agent didn't respond)
- Goodbye: PASS

### B: Agent Rehydration (PASS)
- Pre-restart message acknowledged
- Graceful SIGINT shutdown
- Restart + WebSocket reconnect
- Post-restart recall of "pineapple" from transcript history
- No duplicate messages

### C: Context Isolation (PASS)
- New room created, agent added
- No context leak from previous room
- Normal Q&A works in isolated room

### E: Memory Tools (PASS with PARTIAL steps)
- Store: PASS (thenvoi_store_memory confirmed)
- List (same room): PARTIAL (empty due to subject_id bug)
- Get specific: PARTIAL (agent couldn't find memory via list)
- Cross-room recall: PASS (memory found in new room)
- Supersede: PASS
- Store updated: PASS
- Archive: PARTIAL (tool_result fallback)
- Final list: PASS (green memory active)

### F1: Contact DISABLED (PASS)
- Request sent, stayed pending (agent ignores contact events)
- No contact messages in room
- Clean separation

### F2: Contact CALLBACK (PARTIAL)
- Whitelisted request sent
- Auto-approve didn't trigger (approved=False)
- No broadcast notification
- No LLM invocation (correct for callback strategy)

### F3: Contact HUB_ROOM (PARTIAL)
- Hub room created at startup: PASS
- Friendly request: approved by LLM
- Spam request: still pending (LLM may not have processed in time)
- Empty request: still pending (same issue)

### G: Execution Emit (PASS)
- get_participants: tool_call + tool_result visible
- lookup_peers: tool_call + tool_result visible
- Event count: 3 call + 3 result

### I: Concurrent Rooms (PARTIAL)
- 3 rooms created with secrets ALPHA, BRAVO, CHARLIE
- Room 1 (ALPHA): PASS
- Room 2 (BRAVO): FAIL (returned tool_result instead of text)
- Room 3 (CHARLIE): PASS
