## QA harness results (01_basic_agent, scenarios A-B-C)

Ran the full ABC suite against `01_basic_agent.py` with this branch. The converter fix works — A and C are clean. B is improved but not fully passing.

### Results

| Scenario | Status | Notes |
|----------|--------|-------|
| A: Basic Conversation | **PASS** (6/6) | All steps pass — greeting, domain Q&A, context retention, platform tools, farewell |
| B: Agent Rehydration | **PARTIAL** (3/7) | See below |
| C: Context Isolation | **PASS** (4/4) | No cross-room leaks |

### Scenario B breakdown

| Step | Status | Detail |
|------|--------|--------|
| B1: Remember secret word | PASS | Agent acknowledged "platypus" |
| B2: SIGINT | PASS | Graceful shutdown |
| B3: Restart | PASS | Agent reconnected to room |
| B4: No unsolicited messages | **FAIL** | 1 unsolicited replay after restart |
| B5: Recall secret word (sent while down) | **FAIL** | No response (480s timeout) |
| B6: Summarize conversation | **FAIL** | No response (120s timeout) |
| B7: No extra responses | PASS | — |

### What the fix does right

Before this PR, own-agent text was dropped during rehydration, so the transcript looked like a queue of unanswered user messages. Now the converter keeps own-agent replies as `role="model"`, so the rehydrated transcript matches what would have been accumulated in-memory. The unsolicited replay count dropped (previously multiple replays on 02_custom_instructions, now 1 on 01).

### Remaining issue

After the single unsolicited replay, the agent goes **completely silent** — it never responds to the pending message (sent while agent was down) or the follow-up. The process stays alive but produces no further output. This looks like the ADK runner gets stuck after processing the first post-restart event, possibly an error in the tool loop or session state that blocks subsequent `on_message` calls.

This is likely a separate issue from the converter fix — the converter is doing the right thing now, but the adapter's rehydration flow has a second failure mode that needs investigation.

### Test room

`27555552-d53b-4216-883b-7ff877ea2bd1` / agent `10992cad-f6a1-465e-9025-2dca7ff1fcf0`
