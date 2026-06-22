# QA Report: parlant / 01_basic_agent

## Summary
- **Status:** PASS
- **Date:** 2026-06-03
- **Platform:** app.band.ai
- **LLM:** openai (via Parlant NLP)
- **Agent ID:** d1569971-a83a-4e8d-a7ee-4ba94ba72564
- **Startup:** OK

## Scenario Results

### A: Basic Conversation
**Status:** PASS
**Room:** `9d7db7cb-d206-40c7-bf03-01122fe935bf`
*6-message conversation testing greetings, domain knowledge, context retention, and platform tools*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Send: Greeting | Agent responds with capabilities | Hello @[[6d8e9293-5939-45b9-9de9-8742bafd896d]]! I'm Parlant, your helpful assistant here in the Ban | PASS |
| 2 | Send: Domain question | Agent answers Paris | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The capital of France is Paris. | PASS |
| 3 | Send: Follow-up (context) | Agent references France context | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The second-largest city in France is Marseille. | PASS |
| 4 | Send: List participants | Agent uses band_get_participants or describes participants | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Currently in this chat room: Nir Singher Test (User) and Q | PASS |
| 5 | Send: Lookup peers | Agent uses band_lookup_peers or responds about peers | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Yes, I can look up available agents and users on the platf | PASS |
| 6 | Send: Goodbye | Agent responds with farewell | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Take care! | PASS |

### B: Agent Rehydration
**Status:** PASS
**Room:** `9d7db7cb-d206-40c7-bf03-01122fe935bf`
*Kill and restart the agent, verify it re-joins and responds in the same room*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Pre-restart message | Agent acknowledges | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Got it, I'll remember the word 'armadillo' for when you as | PASS |
| 2 | Stop agent (SIGINT) | Graceful shutdown | graceful=True | PASS |
| 3 | Restart agent | Agent starts and reconnects | started=True | PASS |
| 4 | No unsolicited messages after restart | At most 1 new message(s) (1 pending + 0 in-flight) | 1 new message(s), 0 unsolicited | PASS |
| 5 | Post-restart recall (message sent while down) | Agent recalls 'armadillo' from transcript history | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] The word you asked me to remember earlier is 'armadillo.' | PASS |
| 6 | Post-restart conversation recall | Agent summarizes pre-restart conversation including 'armadillo' | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] Here's a summary of our conversation before the restart: - | PASS |
| 7 | No extra responses after recall | 0 extra messages | No extra responses | PASS |

### C: Context Isolation
**Status:** PASS
**Room:** `9d7db7cb-d206-40c7-bf03-01122fe935bf`
*Create a new room, verify no context leaks from previous conversations*

| # | Action | Expected | Actual | Status |
|---|--------|----------|--------|--------|
| 1 | Create new chat room | New room created | room_id=f8d1319a-0e29-4aae-8e7a-909a9024da25 | PASS |
| 2 | Add agent to new room | Agent added as participant | Added d1569971-a83a-4e8d-a7ee-4ba94ba72564 | PASS |
| 3 | Ask about previous conversation | No content from other rooms | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] So far, you have requested a summary of our conversation.  | PASS |
| 4 | Normal question in new room | Agent answers correctly (4) | @[[6d8e9293-5939-45b9-9de9-8742bafd896d]] 2 + 2 equals 4. | PASS |

## Chat Rooms
- **A: Basic Conversation**: `9d7db7cb-d206-40c7-bf03-01122fe935bf`
- **B: Agent Rehydration**: `9d7db7cb-d206-40c7-bf03-01122fe935bf`
- **C: Context Isolation**: `9d7db7cb-d206-40c7-bf03-01122fe935bf`

## Startup Logs (excerpt)
```
[stdout] 
[stdout]                              ..
[stdout]                           :=++++=-
[stdout]                         :+***+++**+.
[stdout]                       .=*****++++*+=:.
[stdout]                      .=+++*******-
[stdout]              ..:::::...  .::::=++
[stdout]          .-+***#####**+=-..=+=:.
[stdout]        :+######***********. =***=.
[stdout]       =####**###**********+ .*****-
[stdout]      =#******###** v3.3 **+ .******-
[stdout]     :#*******#######****=. =********:
[stdout]     .*#******#*:---=-::..-*********+
[stdout]      -##*##***. -----=++*******++**:
[stdout]       :*###**: =****###**********+:
[stdout]         -+*#- -****************+-
[stdout]           .: .*******++++++==-.
[stdout]             .****+=:.
[stdout]             =+=:.
[stdout]            ..
[stdout] 
[stdout] 
[stderr] [2m2026-06-03T15:41:39.107021Z[0m [[32m[1minfo     [0m] [1m[<main>] Parlant server version 3.3.1[0m
[stderr] 2026-06-03 18:41:39 [INFO] parlant: [2m2026-06-03T15:41:39.107021Z[0m [[32m[1minfo     [0m] [1m[<main>] Parlant server version 3.3.1[0m
[stderr] [2m2026-06-03T15:41:39.107358Z[0m [[32m[1minfo     [0m] [1m[<main>] Using home directory '/Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/examples/parlant/parlant-data'[0m
[stderr] 2026-06-03 18:41:39 [INFO] parlant: [2m2026-06-03T15:41:39.107358Z[0m [[32m[1minfo     [0m] [1m[<main>] Using home directory '/Users/nirs/band/thenvoi-sdk-python/.claude/worktrees/feat-qa-test-harness/examples/parlant/parlant-data'[0m
[stderr] [2m2026-06-03T15:41:39.108251Z[0m [[32m[1minfo     [0m] [1m[<main>] No external modules selected[0m
[stderr] 2026-06-03 18:41:39 [INFO] parlant: [2m2026-06-03T15:41:39.108251Z[0m [[32m[1minfo     [0m] [1m[<main>] No external modules selected[0m
[stderr] [2m2026-06-03T15:41:39.360980Z[0m [[32m[1minfo     [0m] [1m[<main>] Initialized OpenAIService[0m
[stderr] 2026-06-03 18:41:39 [INFO] parlant: [2m2026-06-03T15:41:39.360980Z[0m [[32m[1minfo     [0m] [1m[<main>] Initialized OpenAIService[0m
... (57 more lines)
```
