# INT-829 manual test plan — interrupt / stop / play

Verified against `https://app.band.ai` on 2026-06-15: PLT-944 (platform side) is
deployed, the three control routes are live, and auth uses the `X-API-Key`
header (NOT `Authorization: Bearer`).

## Fixtures (verified)

| Thing | Value |
|---|---|
| Base URL | `https://app.band.ai` |
| Auth header | `X-API-Key: $BAND_API_KEY_USER` |
| Chat (you are **owner**; Tom+Jerry are members) | `430fb827-9d48-476d-8b9f-ec450633c47c` |
| Tom agent_id | `3d5bd75e-6503-40e6-ac49-5acae495e880` |
| Jerry agent_id | `d9378533-dc52-4826-8417-aa9f145f0e1a` |

Control routes (confirmed live):
- Room-scope (room owner; fans out to participating agents): `POST /api/v1/me/chats/{chat_id}/agents/{stop,play}`
- Agent-scope (agent owner; targets one execution): `POST /api/v1/me/agents/{agent_id}/executions/{execution_id}/{stop,play,interrupt}`
- Executions list: `GET /api/v1/me/agents/{agent_id}/executions` → items have `id`, `status`, `stopped_at`.

Tom & Jerry are already running against the worktree build. Tail their logs:
```bash
tail -f /tmp/tom.log    # and in another pane:
tail -f /tmp/jerry.log
```

## 0. Setup
```bash
export BASE="https://app.band.ai"
export BAND_API_KEY_USER="<your band_u_… key>"
export CHAT="430fb827-9d48-476d-8b9f-ec450633c47c"
export TOM="3d5bd75e-6503-40e6-ac49-5acae495e880"
export JERRY="d9378533-dc52-4826-8417-aa9f145f0e1a"

# sanity: should print your user record
curl -s "$BASE/api/v1/me" -H "X-API-Key: $BAND_API_KEY_USER" | python3 -m json.tool | head
```

## Helper: list executions (find the in-flight one)
```bash
curl -s "$BASE/api/v1/me/agents/$TOM/executions" -H "X-API-Key: $BAND_API_KEY_USER" \
  | python3 -c "import sys,json;[print(e['status'].ljust(12), e['id'], 'stopped_at='+str(e['stopped_at'])) for e in json.load(sys.stdin)['data']]"
```
Idle executions show `status: waiting`. A cycle in flight shows a non-waiting,
freshly-`updated_at` status — that's the `execution_id` to interrupt.

## Trigger a reasoning cycle (needed for interrupt)
Send a mention from you (the user) so the agent starts a turn. Use a prompt that
takes a few seconds so you have a window to interrupt:
```bash
# NOTE (verified live): body is nested under "message", and mentions require the
# agent UUID "id" (handle is rejected).
curl -s -XPOST "$BASE/api/v1/me/chats/$CHAT/messages" \
  -H "X-API-Key: $BAND_API_KEY_USER" -H "Content-Type: application/json" \
  -d "{\"message\":{\"content\":\"@Tom write a long, detailed multi-paragraph plan to catch Jerry, step by step\",\"mentions\":[{\"id\":\"$TOM\"}]}}"
```
(Or just type the mention in the Band UI. Letting Tom & Jerry mention each other
creates the runaway loop that stop/play is designed to kill.)

---

## TEST 1 — INTERRUPT (transient: kill one in-flight turn)
Interrupt is **agent-scope only** (no room-scope variant; no UI affordance yet).

1. Trigger a cycle (above).
2. Immediately grab the in-flight `execution_id` with the helper.
3. Fire interrupt:
```bash
export EXEC="<in-flight execution_id from the helper>"
curl -s -w "\n[HTTP %{http_code}]\n" -XPOST \
  "$BASE/api/v1/me/agents/$TOM/executions/$EXEC/interrupt" \
  -H "X-API-Key: $BAND_API_KEY_USER"
```
**PASS criteria**
- `/tmp/tom.log`: `interrupt requested, cancelling in-flight cycle` then
  `cycle interrupted (message …) — nothing sent`.
- Tom posts **no** reply for that turn (partial work dropped).
- Tom answers the **next** mention normally (back to listening) — send another
  mention to confirm.

## TEST 2 — STOP (durable: go quiet until play)
Room-scope (stops every agent in the room) — simplest since you own the chat:
```bash
curl -s -w "\n[HTTP %{http_code}]\n" -XPOST \
  "$BASE/api/v1/me/chats/$CHAT/agents/stop" -H "X-API-Key: $BAND_API_KEY_USER"
```
(Agent-scope variant — stop just Tom's execution:
`POST $BASE/api/v1/me/agents/$TOM/executions/$EXEC/stop`.)

**PASS criteria**
- Executions helper now shows Tom with `stopped_at` non-null.
- `/tmp/tom.log` (if a cycle was running): `stop requested, cancelling in-flight cycle`.
- Send a new mention to Tom → **no response**, and the log shows
  `stopped, skipping message … (left for replay)` and periodically
  `stopped, skipping idle /next poll`.
- Jerry unaffected unless you stopped room-scope (which stops both).

## TEST 3 — PLAY (resume + catch up on what was missed)
```bash
curl -s -w "\n[HTTP %{http_code}]\n" -XPOST \
  "$BASE/api/v1/me/chats/$CHAT/agents/play" -H "X-API-Key: $BAND_API_KEY_USER"
```
**PASS criteria**
- Executions helper shows Tom `stopped_at` back to `null`.
- `/tmp/tom.log`: `Applying control mode=play …` → `Resync sentinel enqueued` →
  `Catching up missed message …`.
- The mention(s) you sent **while stopped** now get answered (rehydration catch-up,
  not dropped).

## TEST 4 — STOP survives reconnect
1. Stop Tom (Test 2). Confirm `stopped_at` set and Tom silent.
2. Restart Tom's process (simulates a reconnect/redeploy):
   ```bash
   pkill -f 03_tom_agent.py
   ( cd /Users/amitgazal/Workspace/thenvoi-sdk-python/.claude/worktrees/gleaming-inventing-twilight \
     && uv run python examples/anthropic/03_tom_agent.py > /tmp/tom.log 2>&1 & )
   ```
3. Send Tom a mention → **still no response** (platform `/next`→204 keeps it quiet;
   the SDK keeps nothing locally). The recovery sweep is also skipped — log will
   NOT show the stuck message being re-processed.
4. `play` (Test 3) → Tom resumes and answers the backlog.

## TEST 5 — dedup / fan-out (optional)
- Re-POST the same control twice quickly. The platform sends one push per call;
  if the same `correlation_id` is delivered twice, `/tmp/tom.log` shows
  `Duplicate control signal … ignored` on the repeat.
- Room-scope stop with both agents present → both Tom and Jerry go quiet
  (`Applying control mode=stop scope=… to N room(s)` in each log).

## Log line cheat-sheet (what the new SDK code emits)
| Event | Logger / line |
|---|---|
| any control arrives | `band.runtime.runtime`: `Applying control mode=… scope=… to N room(s) (correlation_id=… execution_id=…)` |
| interrupt | `band.runtime.execution`: `interrupt requested, cancelling in-flight cycle` → `cycle interrupted (message …) — nothing sent` |
| stop | `stop requested, cancelling in-flight cycle` → `cycle stopped (…)` ; then `stopped, skipping message … (left for replay)` / `stopped, skipping idle /next poll` |
| play | `Applying control mode=play …` → `Resync sentinel enqueued` → `Catching up missed message …` |
| duplicate | `Duplicate control signal … ignored` |
| unknown room | `Control signal (mode=…) for unknown room …; no-op` |

## Cleanup
```bash
pkill -f 03_tom_agent.py ; pkill -f 04_jerry_agent.py
# if you stopped them on the platform, run play first so they aren't left parked.
```

> Note: Tom & Jerry run against **production** and spend Anthropic tokens while
> chatting. Stop the processes when done.
