# Baseline E2E Coverage Plan (live matrix scenarios)

Plan for closing the live end-to-end (E2E) coverage gaps in the baseline toolkit,
derived from the L0-L4 conformance specs but **without** adopting the "levels"
concept. Every deliverable is a scenario-named `@per_adapter` matrix test that
runs across the whole adapter registry, reusing the existing baseline toolkit
(`tests/e2e/baseline/`). The L-labels below are only an organizing device for
mapping the specs; they do not appear in code or file names (the suite names files
by behavior, e.g. `test_context_recall.py`, `test_noisy_room.py`).

## Source specs

These are the Tier 2 (live E2E) portions of the conformance specs. Only the live
scenarios are in scope here; the Tier 1 (isolated, per-PR) tier is out of scope.

- INT-523 (L0): platform adaptation
- INT-524 (L1): custom prompt and custom tools
- INT-525 (L2): conversation context fidelity
- INT-527 (L3): multi-participant chat
- INT-528 (L4): rehydration

## Decisions

1. **Live E2E only.** Build only the Tier 2 (real platform + real small model)
   scenarios. The isolated Tier 1 per-PR tier is not part of this work.
2. **Scenario-named matrix tests, no "levels".** Each gap becomes a
   `@per_adapter` test named for its behavior, landing in
   `tests/e2e/baseline/smoke/matrix/`.
3. **Scope built now:** L0 platform ops, L1 custom prompt, L2 context fidelity, two
   L3 matrix slices (concurrency gate + thin peer delegation), and the L4
   rehydration gaps.
4. **L3 multi-participant: two matrix slices, not the full cascade.** Build (a) the
   concurrency operational gate (N same-adapter instances co-reside in one room, each
   replies) across the **full** matrix - including codex/opencode, the
   shared-`serve`/shared-`CWD` backends whose co-residency this gate most needs to
   probe - and (b) a thin peer-initiated delegation + self-recall test (deliverable
   8). Named routing / delegation / recruitment are already covered by the
   heterogeneous `test_multi_agent_collaboration.py`. The full 4-turn cascade and its
   multi-hop causal-ordering assertion stay out (flakiest/most expensive for marginal
   gain). Description-based routing is **deferred pending a small SDK fix**, not
   blocked: descriptions reach the model only via a proactive `band_get_participants`
   / `band_lookup_peers` tool call, never the passive roster (an SDK drop of a field
   the backend already returns). See the L3 section below.
5. **Assertion policy: tolerant (floors only), with one narrow exception.** Honor
   the toolkit's rule that agents are non-deterministic. Use only:
   - floors (`assert_at_least`),
   - tolerant substrings (`assert_contains_any`),
   - tolerant negatives (`assert_contains_none`),
   - mention-by-metadata (`assert_mentions`),
   - platform-state reads (`user_ops.list_participant_ids`),
   - the token gate (`usage.assert_nonzero_input_and_output`).

   No exact-count or upper-bound assertions on model-driven content (recall,
   phrasing, which tool the model chose).

   **Narrow exception: `assert_at_most` for the loop-suppression runaway guard.**
   Add one upper-bound assertion used *only* after scoping to a sender and
   post-trigger window where exceeding a deliberately high ceiling means the
   adapter is re-dispatching on its own output. It is not used to prove exact
   model reply counts, recall quality, phrasing, or tool choice.

   When a scenario requires several concrete values, assert each required value
   separately over the same scoped reply collection. `assert_contains_any([a, b,
   c])` proves "at least one of these appeared"; it does **not** prove all three
   appeared.

## Coverage map: existing vs gaps

Verified by reading every smoke under `tests/e2e/baseline/smoke/` and grepping for
the target behaviors.

| Scenario (from specs) | Existing coverage | Status |
|---|---|---|
| Agent replies to a mention (matrix) | `matrix/test_adapter_matrix.py::test_per_adapter_replies` | covered |
| Identity + roster probe (name, room id, members, invitable list) | none | GAP |
| Invite a peer + directed message (matrix) | `behavior/test_multi_agent_collaboration.py` recruits a specialist, but heterogeneous fixed set, not matrix, no directed-mention assert | GAP |
| Remove a participant (matrix) | none | GAP |
| No self-triggered send loop (peer bounce, then no runaway) | `behavior/test_peer_actor.py` proves a peer message DOES drive a turn (the opposite property) | GAP |
| Custom tool round-trip (fires + result in reply) | `matrix/test_tool_round_trip.py::test_custom_tool_round_trips` | covered |
| Custom prompt takes effect (marker word in every reply) + coexists with platform tools | none (one parlant-only showcase, not a matrix test) | GAP |
| Single-fact recall in-session | `matrix/test_context_recall.py::test_recalls_within_session` | covered |
| Burst of N turns then N replies (no drop under load) | `behavior/test_agent_scenarios.py` (2 facts, anthropic-only, judge); `matrix/test_noisy_room.py` is a needle-in-haystack recall + ignore-crosstalk test on the agent under test (not a burst/no-drop test) | GAP (no matrix count/floor) |
| Multi-fact recall spanning a long conversation (matrix) | in-session recalls one note; anthropic-only 2-fact judged test | GAP |
| Multi-agent delegation / recruitment / concurrent triage | `behavior/test_multi_agent_collaboration.py` (heterogeneous `@with_adapters` cast) | covered (L3 named routing) |
| 3 concurrent instances of the SAME adapter co-reside, each replies (operational gate) | none | GAP (L3 thin slice, **building**) |
| Description-based routing (pick a peer by its description) | none | DEFERRED (L3: descriptions reach the model only via a proactive `band_get_participants`/`band_lookup_peers` call, not the passive roster; a small SDK fix surfaces them - see L3 section) |
| Peer-initiated delegation + self-recall | none | GAP (L3 thin slice, **building** - deliverable 8) |
| History restored after restart; offline message picked up (single note) | `matrix/test_rehydration_offline.py`, `_partial.py`, `_cross_framework.py` | covered |
| Completed tool call NOT replayed after restart | none (the `Usage` L4 token gate helper exists but no rehydration test uses it) | GAP |
| Already-handled message NOT re-emitted after restart (dedup) | none | GAP |

## Floors-only reconciliation

The specs lean on exact and negative counts. Under the floors-only policy each maps
to a tolerant form, except loop-suppression which uses the narrow `assert_at_most`
exception (see Decisions). One requirement loses real teeth (L2
no-double-processing); the rest are gated or survive well.

| Spec requirement | Tolerant form used | Verdict |
|---|---|---|
| L0 "exactly 1 reply" to the probe | `assert_present` (at least 1) | fine |
| L0 "no self-triggered send loop" (at most 1 reply after Echo's bounce) | `assert_at_most` on the agent's own messages in a FIFO-bounded window, with a high ceiling that only a repeated self-dispatch/runaway should cross | GATED as runaway suppression, not exact one-reply behavior: it catches the adapter re-processing its own output without making model-driven reply batching part of the contract |
| L2 "exactly 9 replies", no dropped turn under burst | reframed as a **processing** no-drop gate: barrier once on the last burst message (FIFO), then assert each message's `delivery_status` is `PROCESSED` | GATED (model-independent) for *processing*, not replies: `PROCESSED` proves each turn was *handled*, NOT that a reply was emitted (`capture.py` warns of this). The reply side is covered separately by the recall probe (Step 2). |
| L2 no duplicate processing (a turn handled twice) | not asserted | UNGATED: an extra reply is most likely model batching, not a re-processed turn, so `assert_at_most` would false-fail a correct adapter |
| L4 dedup "marker count = 1" | post-restart window `assert_contains_none([marker])` (already-handled message not re-answered) | survives well as a tolerant negative |
| L4 "completed invite not replayed" | `list_participant_ids` shows the peer absent + `assert_contains_none` on a re-invite | clean: this is a state check, not a count |
| L4 token split (non-zero replay + new inference) | existing `usage.assert_nonzero_input_and_output()` | helper already exists |

L4's genuinely-new gaps (idempotency and dedup) survive floors-only nicely because
they read platform state and tolerant negatives. L0 loop-suppression is gated by the
narrow `assert_at_most` runaway exception. L2 *processing* no-drop is gated by delivery status
(handling, not replies; the reply side rides the recall probe). Only L2
no-double-processing is left ungated (delivery status proves no dropped turn, but
cannot prove the absence of a duplicate-processed turn).

## Deliverables

All tests are `@per_adapter` (matrix-driven), land in
`tests/e2e/baseline/smoke/matrix/`, and reuse the existing fixtures, drivers, and
assertions. Exactly two small toolkit additions are needed (see "Tools to add"
below): a narrowly-documented `assert_at_most` on the reply observation
(loop-suppression) and `AdapterCell.run_many` (the co-resident multi-instance
helper); every other assertion and driver already exists.

1. **`test_identity_and_roster.py`** (L0 probe)
   Provision an out-of-room `Echo` peer (known name), and a second peer added to the
   room as a member (known name), so every roster check is self-sourced. Probe the
   agent for its name, the room, who is present, and who it could invite.
   **Load-bearing (hard) assertions, all against known/self-sourced values:** the
   reply contains `agent.name` (identity), the room member peer's name (roster), and
   `Echo`'s name listed as invitable (from `user_ops.lookup_peers(not_in_room=room)`,
   whose `Peer.name` is the source). **Soft / best-effort (not gating):** the room
   UUID (small models paraphrase raw UUIDs away) and the test user's display name -
   see the audit refinement. Sourcing the user's display name needs a
   participants-with-names read (`user_ops.list_participant_ids` returns ids only, and
   no toolkit method returns members with names); since it is soft, we skip it by
   default rather than add a driver. This is a **conscious divergence** from spec Step
   1 (which made the room UUID and user display name load-bearing); under floors-only
   both are dropped/softened while the load-bearing identity+roster checks stay
   self-sourced. Reuses `user_ops.lookup_peers`. Scope: `runs_tool_loop=True` (needs
   platform-tool reads).

2. **`test_participant_management.py`** (L0 invite + directed message + remove)
   Drive the agent to invite `Echo`, send it a directed message carrying a marker,
   then remove `Echo`. Assert via `user_ops.list_participant_ids` (state) that Echo
   is present after the invite and absent after the remove. For the directed message,
   use the **coupled** assertion so the mention and the marker must be in the *same*
   message (separate `assert_mentions` + `assert_contains_any` can false-green across
   two messages): `capture.messages.from_sender(agent.id).mentioning(echo.id).assert_contains_any([marker])`.
   Overlaps the recruitment test only slightly (that one is heterogeneous
   `@with_adapters` with no removal); this is the matrix version plus removal. Scope:
   `runs_tool_loop=True`.

3. **`test_loop_suppression.py`** (L0 no self-triggered send loop; **subsumes
   `test_peer_actor`**). Provision `Echo` (a non-running peer) **and add it to the
   room up front** (`provision_room(participants=[agent.id, echo.id])`) - `PeerActor`
   can only post as a participant, so Echo must already be in the room (per the
   toolkit README and `test_peer_actor`'s setup). `Echo` then posts **one directed
   probe** mentioning the agent (a `liveness_probe` carrying a unique marker) - the
   spec's "bounce once", chosen *directed* rather than passive so it reliably elicits
   a reply (a passive bot bounce may draw 0, which the spec allows but
   which makes the positive vacuous). Barrier on the peer message being processed,
   then two assertions from the one peer-driven flow:
   - **Positive (covers `test_peer_actor`, now matrix-wide):**
     `capture.messages.from_sender(agent.id).assert_contains_any([marker])` - the
     peer-authored message reached the agent's inference exactly like a user's and
     drove a real reply. This is precisely `test_peer_actor`'s claim.
   - **Loop-suppression:** after the peer probe settles, snapshot the capture,
     send a follow-up user probe, and barrier on it; per-room FIFO orders the peer
     turn and any loop it spawned ahead of the probe's reply in practice (the
     self-trigger enqueues before the probe - fast self-receipt - though a rare
     interleaving could let the probe land first, which is why the infinite-loop
     timeout is the real backstop; no sleep/silence window). Then run `Replies.assert_at_most(...)` on the agent's
     own messages since the snapshot, using a deliberately high ceiling that a
     normal one-turn reply batch should not cross but a repeated self-dispatch
     will. This is a runaway guard, not an exact "one reply" assertion. An
     infinite loop starves the probe and fails via timeout.
   Reuses `resource_manager.peer(echo).send_message` and `liveness_probe`. Retire
   `test_peer_actor` when this lands (its positive claim is covered here).

4. **`test_custom_prompt.py`** (L1 custom prompt + tool coexistence)
   `runs_tool_loop=True`, `tools=[LOOKUP_TOOL]`, custom prompt injecting a marker
   word. Turn 1: look up an opaque code; assert the code and the marker with two
   separate assertions over the same turn-scoped replies.
   Turn 2: ask who is in the room; assert a **known-named room member** appears (a
   second peer provisioned into the room with a name we control - same self-sourced
   approach as deliverable 1, avoiding the un-sourceable user display name) and the
   marker appears, again as separate assertions over the same scoped collection
   (prompt still active), plus - matching the spec's non-fabrication check -
   `assert_contains_none([nonmember_name])` for a known invitable-but-absent peer (a
   tolerant negative). Reuses the tool-round-trip pattern; proves the prompt takes
   effect and that the custom and platform tools coexist under a custom config.

5. **`test_burst_recall.py`** (L2 burst + spanning recall)
   Plant `N` facts (parametrized, default 6) in a no-wait burst (sends may be
   `gather`ed - they are independent REST calls, not waiters). This is a **processing
   no-drop** gate, not a reply-count gate: `PROCESSED` proves each turn was *handled*,
   not that a reply was emitted (`capture.py` warns `processed` does not imply a
   reply). Verify no dropped turn via **delivery status** without violating the
   one-waiter-per-capture rule: **barrier once** on the *last* burst message
   (`wait_for_processed(last, agent.id)`; per-room FIFO means the last being processed
   proves every earlier one was), then assert each burst message's
   `capture.delivery_status(mid, agent.id)` is `PROCESSED` (non-waiting reads, so no
   concurrent waiters). Model-independent (immune to ack-batching or a skipped reply),
   no sleep/silence window. Then send a single recall probe and assert facts
   spanning the conversation are present: an **early** fact, a **mid-history** fact,
   and a **recent** fact. Assert each required fact separately over the recall
   turn's scoped replies; `assert_contains_any([early, mid, recent])` would be too
   weak because it passes after only one fact. Single-fact recall cannot tell
   "kept the whole history" from "kept only a recent window". Combines the two
   L2 steps into one flow, as the spec does; a stronger sibling of
   `test_recalls_within_session`
   (which recalls one note over two sequential turns). Scope: full matrix minus
   `crewai_flow` (terminal echo, no memory), matching `test_recalls_within_session`;
   `codex` / `opencode` are kept (in-session recall works; only their cross-run
   `/context` rehydration differs). No custom tools, so no `runs_tool_loop`
   restriction.

6. **`test_rehydration_idempotency.py`** (L4) - two tests in one file, so crewai
   (whose usage is cumulative, not per-turn) keeps the idempotency coverage while
   only the token-split test excludes it.

   Both reuse the `cell.run_as`-twice stop/cold-restart lifecycle already proven by
   `test_context_recall::test_recalls_after_rejoin` and `test_rehydration_partial`.
   **Offline pickup is driven by the boot-drain** (verified in `src/band`): on cold
   boot the SDK runs `_synchronize_with_next()`, draining the server `/next`
   unprocessed queue before the WS queue, and `mark_processed` removes handled
   messages from it. So an offline message is answered on boot with **no new
   trigger**, an already-handled message is excluded from `/next` (not re-answered),
   and a completed tool call's triggering message is excluded (not re-run).
   **"Completed" is load-bearing:** `mark_processed` runs only *after* the full turn
   (tool loop included), so the idempotency assertions require a **clean** stop - the
   toolkit's `cell.run_as`-exit, not an abrupt kill. A crash *mid*-tool-call leaves
   the message in `processing` and the SDK re-runs it by design (crash recovery); that
   abrupt-kill / crash-dedup case is the spec's per-adapter "expand coverage", out of
   baseline scope here.

   **Subtlety - open `reply_capture` *before* `run_as`** in run 2: the boot-drain
   answers the offline question during startup, so the observer must already be
   subscribed or the reply races past the capture (the boot-drain analogue of
   subscribe-before-send). Barrier via `wait_for_processed(offline_mid, ...)`.

   - **Test A - idempotency** (`runs_tool_loop=True`; includes crewai). Run 1: seed
     facts + invite `Echo` (a completed `band_add_participant` call), then a handled
     marked message; assert the marker present pre-restart and `Echo` in the room via
     `list_participant_ids`. Down: remove `Echo` via API (state check absent), queue
     the offline question (not barriered). Run 2 (cold): boot-drain answers the
     offline question - assert each required recalled fact separately (history
     restored + offline picked up), `assert_contains_none([marker])`
     (already-handled not re-emitted),
     and `Echo` still absent via `list_participant_ids` (completed tool not replayed).
   - **Test B - restart token split** (`runs_tool_loop=True`, `exclude={Adapter.CREWAI}`,
     `features=usage_features()`). Same stop/restart; read the post-restart turn's
     usage scoped by a **server timestamp** `since` (the last run-1 message's
     `inserted_at`, not client `datetime.now`, to avoid clock skew), then
     `usage.assert_nonzero_input_and_output()` - non-zero input (replayed `/context`)
     AND output (new inference). crewai is excluded with a documented reason
     (cumulative usage → token gate N-A; its rehydration recall is covered by
     `test_recalls_after_rejoin`).

   `runs_tool_loop=True` already excludes codex/opencode/crewai_flow, subsuming the
   session-resuming-backend exclusion the other rehydration tests spell out.

7. **`test_concurrent_instances.py`** (L3: same-adapter co-residency)
   The operational gate, standalone and model-light. Stand up 3 instances of the
   current matrix adapter (distinct identities) in one room via the new
   `cell.run_many(3)` - which starts them **concurrently** (mirroring the `agents`
   fixture's `TaskGroup`), so a real port/lock-file collision races rather than being
   masked by serial starts - fire a mention at each **concurrently** (`asyncio.gather`
   the *sends* - they are independent REST calls), then **await the delivery barriers
   sequentially** (`for i in instances: await capture.wait_for_processed(mid_i, i.id)`)
   - one waiter at a time per capture (`capture.py`), so the barriers must not be
   `gather`ed even though the sends were. Then assert each instance replied
   (`capture.messages.from_sender(i.id).assert_present()`).
   Collisions fail loud for free: an instance that can't start makes `run_many` raise
   (test errors); one deadlocked on a shared resource never reaches `PROCESSED` (its
   barrier times out); one that starts but can't reply fails `assert_present`. All
   assertions are floors-only. Scope: the **full matrix** via bare `@per_adapter()` -
   **including** `codex` and `opencode`. Those shared-`serve` / shared-`CWD` backends
   are exactly the co-residency the spec's "N instances, no port/lock collision"
   requirement targets, so a backend that cannot host 3 co-resident instances **fails
   loud here** (the spec: "if a second instance cannot start, L3 cannot run") - that
   red cell is a real conformance signal, not a test to suppress. `K=3` in the test
   (the spec's Test Agent + Calc + Greeter trio); `run_many`'s `count` stays general.

8. **`test_peer_delegation.py`** (L3 thin slice: peer-initiated delegation + self-recall)
   Two co-resident same-adapter instances `A` and `B` via `cell.run_many(2)`
   (`runs_tool_loop=True`, so it fans over the tool-loop adapters - the peer routing
   mention needs the `band_send_message` path). Turn 1 seeds a value `V` into `B`'s
   own context (a `unique_marker`); barrier. Turn 2 addresses **`B` directly** (not an
   orchestrator) - "ask `A` to <use `V`> and report back" - so the delegation is `B`'s
   own decision (peer-initiated). Load-bearing floors-only assertions from the one
   flow:
   - **Peer-initiated routing mention + self-recall (coupled):**
     `capture.messages.from_sender(B.id).mentioning(A.id).assert_contains_any([V])` -
     `B` emitted a *real* routing mention of `A` (metadata, not plain text) whose body
     carries the value `B` recalled from its **own** Turn-1 context. Coupled so the
     mention and the recalled value are in the same message.
   - **Delegate responded:** `capture.messages.from_sender(A.id).assert_present()`.
   - **Round-trip value (soft, not gating):** `B`'s final message to the user carrying
     `A`'s computed result - the flakiest hop on a small model, so kept soft (or a
     follow-up turn). This mirrors the spec's Turn 4 minus the multi-hop cascade.
   Reuses `run_many` (no new primitive), `mentioning`, coupled `assert_contains_any`,
   `assert_present`. `K=2` here; `run_many`'s `count` stays general.

## Tools to add (the ONLY new plumbing)

Exactly two new primitives, each confirmed absent today (grepped + read) and each
composing existing ones. **No new fixtures or drivers.** Everything else is reused
(see the Test → tools map). `assert_at_most` is used by one test (loop suppression);
`run_many` by two (concurrent instances + peer delegation).

| New tool | Location | Used by | Why it's needed / shape |
|---|---|---|---|
| `Replies.assert_at_most(n)` | `toolkit/observations/replies.py` | `test_loop_suppression` (only) | The one upper-bound assertion (the narrow count exception). Placed on `Replies`, **not** the shared `ContentAssertions` mixin - that base is explicitly "a floor, never an exact count" and is shared with `Events`, so an upper bound there would erode the contract for every collection. Localizing it on `Replies` (where loop-suppression operates) keeps the shared base floors-only. Loud docstring: only for sender/window-scoped runaway guards, never for model-driven exact reply counts. Shape mirrors `assert_at_least`. |
| `AdapterCell.run_many(count, *, labels=, prompt=, features=, tools=)` | `toolkit/provisioning.py` | `test_concurrent_instances`, `test_peer_delegation` | Co-resident K same-adapter instances in one room. An async context manager that provisions `count` distinct identities and runs a fresh adapter per identity, yielding the running identities. **The multi-instance-in-one-room machinery already exists** - the `agents` fixture (`fixtures/agents.py`) runs its group via an `AsyncExitStack` + a `TaskGroup` (concurrent start); factor that into a shared helper both call, and start instances **concurrently** (a serial start is a weaker port/lock-collision probe). Composes the cell's existing `provision` + `run_as`; distinct identities mean `track_running` never conflicts. Uniform steering now; per-instance prompts (role-differentiated Calc/Greeter) a later extension. |

### Samples to add (driving glue only - no new assertions/fixtures)

Small additions to `tests/e2e/baseline/smoke/samples/sample_agents.py`, alongside
the existing `REPLY_PROMPT` / `REMEMBER` / `RECALL` / `unique_marker` /
`liveness_probe`, reusing `unique_marker`:

- a marker-bearing custom system prompt (L1 custom prompt),
- seed-facts driving messages for the burst (L2),
- a roster-probe driving message (L0 identity),
- a directed-message marker + invite phrasing (L0 participant mgmt; reuse the
  "add agent (id ...)" phrasing from `test_multi_agent_collaboration`),
- a peer-delegation driving message (L3 deliverable 8: seed value `V` into `B`, then
  "@B ask A to <use V> and report back").

L1 reuses `LOOKUP_TOOL` / `LOOKUP` / `ACCESS_CODES` from `sample_tools.py` (no new tool).

## Test → tools map (existing reused vs new)

Every deliverable is the *scenario*; the plumbing is reused. New plumbing appears
only in the two cells that reference the tools above.

| Deliverable | Existing tools reused | New tool |
|---|---|---|
| identity + roster | `user_ops.lookup_peers` (`Peer.name`), out-of-room peer + a known-named member peer via `provision_agent`, `reply_capture`, `wait_for_processed`, `Replies.assert_contains_any` | - |
| participant mgmt | `band_add_participant`/`band_remove_participant` (agent tools), `user_ops.list_participant_ids`, coupled `from_sender(agent).mentioning(echo).assert_contains_any([marker])`; invite phrasing from `test_multi_agent_collaboration` | - |
| loop suppression (subsumes `test_peer_actor`) | `resource_manager.peer(echo).send_message` (Echo added to room up front), `liveness_probe`, `from_sender().assert_contains_any` (the positive), `snapshot`/`since`, FIFO probe barrier | `Replies.assert_at_most` |
| custom prompt | `LOOKUP_TOOL`/`LOOKUP`/`ACCESS_CODES` (`sample_tools`), known-named member peer, `tools=`/`prompt=` steering, `assert_contains_any` | - |
| burst recall | `reply_capture`, single `wait_for_processed` on the last (FIFO) + per-message `delivery_status` reads, `snapshot`/`since`, `assert_contains_any` | - |
| concurrent instances | `cell.provision`/`run_as`, `provision_room`, `gather` the *sends* + **sequential** barriers (the required pattern from `ReplyCapture`; do not copy older concurrent-waiter examples), `from_sender().assert_present()` | `AdapterCell.run_many` |
| peer delegation (deliverable 8) | `cell.run_many(2)`, coupled `from_sender(B).mentioning(A).assert_contains_any([V])`, `from_sender(A).assert_present()` | `AdapterCell.run_many` (reused) |
| rehydration idempotency (A) | `cell.run_as`-twice (`test_recalls_after_rejoin`/`test_rehydration_partial`), capture-before-boot, `wait_for_processed`, `list_participant_ids`, `assert_contains_any`/`assert_contains_none` | - |
| restart token split (B) | same lifecycle + `capture.usage(since=)`, `Usage.assert_nonzero_input_and_output` (already the L4 gate) | - |

**Refinements found during the audit:**
- `assert_at_most` lives on `Replies`, not the shared floors-only `ContentAssertions`
  (see Tools to add).
- token-split `usage(since=)` must be a **server** timestamp (last run-1 message's
  `inserted_at`), not client `datetime.now`, per the `capture.usage`/`tool_calls`
  `since` contract.
- identity+roster's "room UUID in the reply" is a model-echo of a raw UUID, which
  small models often paraphrase away; treat the agent name + a member name + an
  invitable-peer name as the load-bearing tolerant checks, and keep the UUID check
  soft (or drop it) to avoid flakiness.

## Implementation reference

This is the implementation reference for the work below. If a deliverable appears
to need code outside the paths named here, update this plan first and explain why;
otherwise the implementation should stay on these extension seams. The goal is for
each test to read as the scenario only, with provisioning, lifecycle, waits,
assertions, tool translation, and cleanup staying in the toolkit.

### Reuse lookup

| Need | Reuse | Worked example / source of truth |
|---|---|---|
| Fan a scenario across adapters | `@per_adapter(...)` from `tests/e2e/baseline/agents.py` | `smoke/matrix/test_context_recall.py`, `smoke/matrix/test_tool_round_trip.py` |
| Select tool-loop-capable adapters | `@per_adapter(runs_tool_loop=True)` | `smoke/matrix/test_tool_round_trip.py` |
| Stop/restart one identity | `cell.provision(...)` + `cell.run_as(identity)` twice | `smoke/matrix/test_context_recall.py::test_recalls_after_rejoin` |
| Provision deterministic peer identities | `resource_manager.provision_agent(...)` | `guards/test_user_ops.py`, `smoke/matrix/test_loop_suppression.py` |
| Put known participants in a room | `resource_manager.provision_room(participants=[...])` | Every matrix smoke; use this instead of raw REST setup |
| Drive a peer-authored message | `resource_manager.peer(peer).send_message(...)` | `smoke/matrix/test_loop_suppression.py`; peer must already be in the room |
| Act as the test user | `user_ops.send_message`, `add_participant`, `remove_participant`, `list_participant_ids`, `lookup_peers` | `toolkit/user_ops.py`; do not instantiate raw clients in tests |
| Observe replies without races | `async with reply_capture(room_id) as capture:` before sending | All smokes |
| Wait for a turn to finish | `await capture.wait_for_processed(mid, agent.id)` | One waiter at a time per capture; do not `gather` waiters |
| Prove a burst was handled | Wait once on the last message, then read `delivery_status(...)` for earlier ids | `toolkit/capture.py` FIFO contract |
| Scope assertions to later replies | `mark = capture.messages.snapshot()` then `.since(mark)` | `smoke/matrix/test_context_recall.py` |
| Scope assertions to one sender | `capture.messages.from_sender(agent.id)` | `smoke/matrix/test_loop_suppression.py` |
| Couple routing + content | `.from_sender(agent.id).mentioning(peer.id).assert_contains_any([marker])` | Use this when the mention and marker must be in the same reply |
| Read tool calls / usage after a turn | `capture.tool_calls(..., since=server_ts)`, `capture.usage(..., since=server_ts)` | `smoke/behavior/test_isolation.py`, `smoke/inspection/test_usage.py` |
| Reuse custom tools | `LOOKUP_TOOL`, `WEATHER_TOOL`, `EXECUTION_REPORTING` from `smoke/samples/sample_tools.py` | `smoke/matrix/test_tool_round_trip.py` |
| Reuse prompt / feature shapes | `REPLY_PROMPT`, `REMEMBER`, `RECALL`, `memory_features`, `usage_features` from `smoke/samples/sample_agents.py` | Context, rehydration, memory, usage smokes |

### Scenario skeleton

New matrix tests should follow this shape unless the deliverable explicitly owns a
manual lifecycle through `cell`:

```python notest
@per_adapter(..., prompt=..., features=..., tools=...)
@pytest.mark.asyncio(loop_scope="session")
async def test_behavior(
    agent: ProvisionedAgent,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    room_id = await resource_manager.provision_room(
        title=f"e2e-behavior-{agent.adapter_id}",
        participants=[agent.id],
    )

    async with reply_capture(room_id) as capture:
        mark = capture.messages.snapshot()
        mid = await user_ops.send_message(
            room_id,
            "...",
            mention_id=agent.id,
            mention_name=agent.name,
        )
        await capture.wait_for_processed(mid, agent.id)

    capture.messages.since(mark).from_sender(agent.id).assert_contains_any([marker])
```

Manual lifecycle tests should stay on the `AdapterCell` API, not create adapters or
`Agent` objects directly:

```python notest
@per_adapter(..., prompt=...)
@pytest.mark.asyncio(loop_scope="session")
async def test_restart_behavior(
    cell: AdapterCell,
    resource_manager: ResourceManager,
    user_ops: UserOps,
    reply_capture: CaptureFactory,
) -> None:
    identity = await cell.provision(label=f"restart-{cell.adapter_id}")
    room_id = await resource_manager.provision_room(participants=[identity.id])

    async with cell.run_as(identity):
        ...

    offline_mid = await user_ops.send_message(
        room_id,
        "...",
        mention_id=identity.id,
        mention_name=identity.name,
    )

    async with reply_capture(room_id) as capture:
        async with cell.run_as(identity):
            await capture.wait_for_processed(offline_mid, identity.id)
```

For boot-drain rehydration, keep the `reply_capture` open **before** the second
`run_as` enters. The offline reply can happen during startup, before any post-boot
user trigger.

### Shared driving glue

Put repeated user-facing instructions, prompts, and marker builders in
`tests/e2e/baseline/smoke/samples/sample_agents.py`. A deliverable may add small
helpers there when the wording is part of the scenario contract:

- custom-prompt marker prompt,
- roster probe,
- burst fact seed / recall wording,
- directed invite / remove wording.

Keep them deterministic and parameterized. Prefer a function that takes a marker or
peer name over inline f-strings spread across tests. Reuse `unique_marker(...)` for
all verbatim assertions. Do not add a new fixture for phrasing.

### Adding custom tools

Most deliverables here should not add a custom tool. `LOOKUP_TOOL` already proves
opaque tool round-trip behavior and should be reused for L1. If a future scenario
genuinely needs a new custom tool, add exactly one `ToolSpec` in
`tests/e2e/baseline/smoke/samples/sample_tools.py`:

```python notest
class ExampleInput(BaseModel):
    """Return a deterministic value for a test key."""

    key: str


def _example(args: ExampleInput) -> str:
    return EXAMPLE_VALUES[args.key.lower()]


EXAMPLE_TOOL = ToolSpec(ExampleInput, _example)
```

Rules for custom tools:

- deterministic, local, no network or sleeps;
- opaque outputs stored in one source map (`ACCESS_CODES`-style), so assertions
  reference the same source as the handler;
- pass `tools=[EXAMPLE_TOOL]` through `@per_adapter` / `@with_adapters`;
- add `**EXECUTION_REPORTING` only when the test reads `capture.tool_calls`;
- never pass framework-native tool objects from a test. Builders own translation.

### Adding toolkit primitives

Toolkit primitives live with the data or lifecycle they own. Do not add new fixtures
for this plan.

**`Replies.assert_at_most`**

- Location: `tests/e2e/baseline/toolkit/observations/replies.py`.
- Scope: `Replies` only; do **not** add it to `ContentAssertions`.
- Contract: sender/window-scoped runaway guards only, never model-driven exact
  reply counts.
- Usage here: loop-suppression after scoping to the sender/window under test, with
  a deliberately high ceiling that repeated self-dispatch should cross.
- Shape: mirror `assert_at_least`, with a loud docstring and a diagnostic that
  includes the captured message contents.
- Tests: add a small PR-run unit test outside `tests/e2e/**` if practical (for
  example in `tests/framework_conformance/test_toolkit_helpers.py`) because pure
  policy/helper tests inside `tests/e2e/**` are skipped unless E2E is enabled.

**`AdapterCell.run_many`**

- Location: `tests/e2e/baseline/toolkit/provisioning.py`.
- Scope: an async context manager on `AdapterCell`; no fixture.
- Contract: provision `count` distinct identities and run one fresh adapter per
  identity using `AsyncExitStack`; yield `list[ProvisionedAgent]`.
- **Reuse, don't re-invent:** the `agents` fixture (`fixtures/agents.py`) already runs
  a multi-member group in one room via an `AsyncExitStack` + a `TaskGroup` that starts
  members **concurrently**. Factor that into a shared helper both `run_many` and the
  fixture call, rather than hand-rolling a second `AsyncExitStack` loop.
- **Start concurrently:** a serial list-comprehension start is a weaker probe of the
  port/lock-file collisions the concurrency gate exists to catch; start the instances
  concurrently (as the fixture does) so a real collision races.
- Build from existing methods: `await self.provision(label=...)` plus
  `stack.enter_async_context(self.run_as(identity, ...))`.
- Label behavior: default labels should include the adapter id and index; explicit
  labels must be length `count` so names do not collide.
- Steering behavior: `prompt`, `features`, and `tools` should pass through to
  `run_as`, preserving the cell defaults when omitted.
- Failure behavior: let startup/provision failures raise; teardown remains owned by
  the context managers and `ResourceManager`.
- Tests: add a focused guard or PR-run helper test only if it can avoid live
  platform work. The live proofs are `test_concurrent_instances.py` and
  `test_peer_delegation.py`.

Illustrative shape (composition only - the real implementation should reuse the
`agents` fixture's concurrent-start helper, per the bullets above):

```python notest
@asynccontextmanager
async def run_many(
    self,
    count: int,
    *,
    labels: list[str] | None = None,
    prompt: str | None = None,
    features: AdapterFeatures | None = None,
    tools: list[ToolSpec] | None = None,
) -> AsyncGenerator[list[ProvisionedAgent], None]:
    if count <= 0:
        raise ValueError("run_many count must be positive")
    if labels is not None and len(labels) != count:
        raise ValueError("run_many labels length must match count")

    identities = [
        await self.provision(label=(labels[i] if labels else f"{self.adapter_id}-{i}"))
        for i in range(count)
    ]
    async with AsyncExitStack() as stack:
        running = [
            await stack.enter_async_context(
                self.run_as(identity, prompt=prompt, features=features, tools=tools)
            )
            for identity in identities
        ]
        yield running
```

### Barrier and timestamp rules

- Open `reply_capture` before the trigger that can produce the observed reply.
- Do not run multiple `capture.wait_for_*` calls concurrently on the same capture;
  `ReplyCapture` has one internal nudge event.
- For burst processing, wait on the last message and inspect earlier statuses with
  non-waiting `delivery_status(...)` reads.
- `PROCESSED` proves the handler completed; a reply assertion is separate.
- Use `snapshot()` / `since()` for in-memory reply scoping, never manual list
  slicing in tests.
- Use server timestamps (`message.inserted_at`) for durable REST reads
  (`tool_calls`, `usage`, events), never `datetime.now()`.

### Anti-bolt-on checklist

Before opening a PR for any deliverable:

- The test imports from `tests.e2e.baseline.agents`, samples, toolkit fixtures, and
  observation collections; it does not instantiate raw REST/WS clients or adapters.
- Adapter selection uses `Adapter` enum members and registry filters, never strings
  or hand-written adapter lists.
- New shared wording lives in `sample_agents.py`; new custom tools, if any, live in
  `sample_tools.py`.
- Assertions are on `Replies`, `ToolCalls`, `Usage`, `Memories`, or explicit
  platform-state reads. Raw `assert` is reserved for simple state membership or
  guard diagnostics.
- No sleeps, silence windows, strict transcript ordering, exact model phrasing, or
  upper bounds on model-driven content.
- If a matrix test subsumes a hardcoded/smaller test, retire the old test in the
  same change.
- Pure helper/policy tests go outside `tests/e2e/**`; live behavior proofs stay
  under `tests/e2e/baseline/smoke/`.

## Consolidation and retirements (no duplication, no stacking)

The baseline rule is explicit: *a matrix test supersedes a hardcoded-list one;
delete what it subsumes rather than stacking.* Applying it to what these new matrix
tests cover:

| Existing test | Disposition | Reason |
|---|---|---|
| `smoke/behavior/test_agent_scenarios.py::test_agent_recalls_earlier_facts` | **RETIRE** | Anthropic-only, judge-based 2-fact burst recall. Fully subsumed by `test_burst_recall` (matrix, deterministic, multi-fact, delivery-status no-drop). The judge worked-example is preserved by `test_two_agents_greet_each_other` in the same file, so retiring this loses no toolkit demonstration. |
| `smoke/behavior/test_peer_actor.py::test_peer_message_drives_agent_turn` | **RETIRE** | Its positive claim (a peer-authored message drives a real reply) is folded into `test_loop_suppression`, which uses a *directed* peer probe and asserts `from_sender(agent).assert_contains_any([marker])` across the matrix - strictly broader than the anthropic-only original. The `PeerActor` usage is preserved there as the driving mechanism. |
| `test_recalls_within_session` (matrix) | **KEEP** | Single-fact, 2-turn, cheaper and more robust than `test_burst_recall`; a good fast canary. Not a hardcoded-list test, so the supersede rule does not apply. |
| `test_rehydration_offline` / `_partial` / `_cross_framework` | **KEEP** | Cover history-restore across topologies (cold-boot, partial-reboot, foreign-peer) that idempotency does not; idempotency *adds* dedup + completed-tool + token-split, it does not replace them. |
| `test_multi_agent_collaboration` (recruitment/delegation/triage) | **KEEP** | Heterogeneous cross-framework routing/delegation - a different purpose from the same-adapter concurrency gate and the matrix participant-mgmt test. |
| `test_tool_round_trip` | **KEEP** | Owns the custom-tool fire+result round-trip; `test_custom_prompt` deliberately does not re-assert it. |
| `smoke/adapters/test_parlant.py` (custom-prompt showcase) | **KEEP** | parlant is not a matrix cell (`NON_AGENT_ADAPTERS`), so the matrix `test_custom_prompt` does not cover it. |

Net retirement: **two** tests - `test_agent_recalls_earlier_facts` (subsumed by
`test_burst_recall`) and `test_peer_actor` (subsumed by `test_loop_suppression`).

## Matrix scoping (single source of truth, no hardcoded lists)

- Tests that drive platform or custom tools: `@per_adapter(runs_tool_loop=True)`
  (the current registry flag for adapters that can execute the SDK tool loop).
  If local custom-tool support and platform-tool support diverge later, add a
  registry selector for that capability instead of hardcoding adapter lists.
- Rehydration idempotency (Test A): `runs_tool_loop=True` (needs the invite tool),
  which already excludes the session-resuming backends - no explicit `exclude`
  needed. Test B adds `exclude={Adapter.CREWAI}` (cumulative usage).
- Same-adapter concurrency gate: the **full matrix** via bare `@per_adapter()`,
  **including** `codex`/`opencode` - they are the collision-prone backends this gate
  is meant to probe, so a backend that cannot co-reside fails loud (a real L3 signal).
- Peer delegation (deliverable 8): `@per_adapter(runs_tool_loop=True)` (the routing
  mention needs the tool loop).
- Everything else: the full matrix via bare `@per_adapter()`.

Lane scoping (`BAND_E2E_LANE`) shards these across CI jobs automatically; no
workflow edits are needed because a new matrix test rides the existing lanes.

## L3: challenges and the two matrix slices

L3 was dissected separately because "missing" meant several different things, and the
distinction is what makes two thin matrix slices (deliverables 7 and 8) the right call
rather than the full 4-turn cascade.

**Description-routing is deferred pending a small SDK fix, not a hard product block.**
The **passive** roster carries no descriptions: `runtime/formatters.py` renders it as
`- @handle - name (type)` and `runtime/participant_tracker.py` keeps only
`id/name/type/handle`. But this is an **SDK drop of a field the backend already
returns** - the in-room participant endpoint yields `ChatParticipantDetails.description`,
which `load_participants` discards before formatting. And descriptions **do** reach the
model through two tool-call paths: `band_get_participants` returns the full
`ChatParticipantDetails` (with description) and `band_lookup_peers` returns `Peer`
objects carrying `description`. So description-based routing (spec Turn 2) is not
categorically impossible - it is **unreliable off the passive roster** and reachable
only if the model proactively calls one of those tools (model-dependent on a small
model). The right move is a **small SDK fix** (stop discarding `description` at
`load_participants` / the participant tracker and render it in the roster) - **file a
Linear issue** - after which a description-routing matrix test becomes reliably
buildable. Deferred until then; not built now.

**Test-framework gaps (in the baseline toolkit):**

- REAL, new: no first-class topology for "K co-resident instances of the current
  matrix adapter in one room." Addressed by `AdapterCell.run_many` (see Toolkit
  additions), which should **reuse** the `agents` fixture's existing `AsyncExitStack`
  + `TaskGroup` co-residency machinery rather than hand-roll a second copy.
- MINOR: `provision_agent` hardcodes the description (cosmetic; only matters once the
  SDK surfaces descriptions and a description-routing test is built).
- NICE-TO-HAVE: multi-hop cascade barriers are ad-hoc (`wait_until` per test); no
  reusable "value reached sender X" helper (only needed if the full cascade is ever built).

**Already expressible (not gaps):** distinct roles via `run_as(prompt=...)`; "each
instance replied" via `from_sender().assert_present()`; peer routing via
`from_sender(x).mentioning(y)`; self-recall via a coupled
`from_sender(x).mentioning(y).assert_contains_any([V])`; a real resource collision
surfaces as a startup exception or missing reply (fails the test).

**Challenge inventory:** topology (LOW, addressed by `run_many`); per-instance
descriptions -> passive roster (MEDIUM, a small SDK fix, not a hard block); assertions
vs floors-only (MEDIUM, re-expressed tolerantly); small-model flakiness of the full
4-hop cascade (HIGH, inherent - why the cascade stays out); cost/scale (MEDIUM, most
expensive scenario); overlap with the existing collab tests (named routing already
covered).

**In scope now:** the full-matrix concurrency operational gate (deliverable 7,
including codex/opencode) and a thin peer-initiated delegation + self-recall test
(deliverable 8). **Deferred (SDK fix first):** description routing. **Out (flaky /
marginal):** the full 4-turn cascade and its multi-hop causal-ordering assertion.
**Already covered:** named routing / delegation / recruitment (`test_multi_agent_collaboration`).

## Implementation order

Dependency order, smallest first:

1. Shared sample additions (prompts and driving messages).
2. Toolkit additions (`assert_at_most`, `AdapterCell.run_many`).
3. L0: identity/roster, participant management, loop suppression - and **retire**
   `test_peer_actor` (subsumed by loop suppression; see Consolidation).
4. L1: custom prompt.
5. L2: burst recall - and **retire** `test_agent_recalls_earlier_facts` (subsumed;
   see Consolidation and retirements).
6. L3 slice 1: full-matrix same-adapter concurrency gate (deliverable 7).
7. L3 slice 2: thin peer-initiated delegation + self-recall (deliverable 8, reuses
   `run_many`).
8. L4: rehydration idempotency (Test A + Test B).

Separately (not a test deliverable): **file the SDK issue** to surface participant
descriptions in the passive roster, which unblocks a future description-routing test.

Each test is validated live per the baseline run command:

```
E2E_TESTS_ENABLED=true uv run pytest tests/e2e/baseline/ -v -s --no-cov
```

(or a single adapter with `-k <adapter>`; a single lane with `BAND_E2E_LANE=<lane>`).

## Implementation team (sub-agent roles and orchestration)

Each deliverable is built by a small team of specialized sub-agents that a **Product
Manager** orchestrates. Roles are deliberately separated so that implementation,
review, standards, architecture-fit, and live QA are **distinct gates** - no agent
blesses its own work. The PM spawns the role agents (via the Agent tool), routes
their outputs, and advances the plan; the specialist skills already in this repo are
the tooling each role uses.

| Role | Owns | Gate it enforces | Tooling |
|---|---|---|---|
| **Product Manager** (orchestrator) | Sequencing the Implementation order, spawning/coordinating the role agents, integrating outputs, deciding "done", keeping this doc the single source of truth | A deliverable advances only after every gate below passes; any scope change lands in this doc first | Agent tool (spawn), task tracking |
| **System Architect** | Pre-implementation review of each deliverable against (a) **this plan** (Test -> tools map, floors-only, only the two sanctioned new primitives) and (b) **current system operability** (does the SDK/toolkit actually support it - e.g. the `/next` boot-drain, `run_many` composing `provision`/`run_as`, the target adapters' capabilities) | "Plan-fit + operable" before code is written; separates a real product gap from a toolkit gap (as with L3 description-routing) | `Plan` agent, `Explore`, reads `src/band` + toolkit |
| **Engineer** | Implementing the deliverable TDD-style on the baseline toolkit, reusing primitives per the Test -> tools map, adding only the two sanctioned tools | Reuse-first: no reinvented plumbing; the test is the scenario, not scaffolding | `my-write-e2e-test` skill |
| **Peer Reviewer** | Reviewing the diff for correctness and reuse (did it duplicate an existing test/helper? follow existing patterns?) | No duplication; matches toolkit conventions | `code-review` skill |
| **Principal Engineer** | Standards gate: clean, elegant, single-source-of-truth, matches surrounding style; **blocks bolt-on patches / band-aids** | No workaround merges; a fix addresses the cause, not the symptom | `simplify` + `code-review` at a high bar |
| **QA** | Running the test **live** against the real platform + LLMs and debugging failures (adapter/product bug vs test-case assumption vs toolkit bug vs LLM flakiness) | Green live across its matrix scope, or a real gap escalated with evidence | `my-run-e2e-live` + `my-investigate-test-error` skills |

**Per-deliverable loop** (PM-driven, one deliverable at a time per the order):

1. **Architect** reviews the deliverable against the plan and system operability -> go / adjust (or raise a product-vs-toolkit gap to the PM).
2. **Engineer** implements TDD, reuse-first, adding only sanctioned plumbing.
3. **Peer Reviewer** checks correctness + no duplication -> fixes.
4. **Principal** enforces the standards gate (clean, elegant, no bolt-ons) -> fixes.
5. **QA** runs it live and debugs to green, or escalates a genuine adapter/product/toolkit gap with evidence.
6. **PM** integrates, marks the deliverable done, lands its **consolidation retirement** in the same change as the superseding test, and advances to the next.

The PM never lets a deliverable skip a gate; a red QA run that traces to a real
product gap (not a test bug) is escalated to the Architect and recorded in this doc,
not patched around.

## Explicitly out of scope

- The Tier 1 isolated per-PR tests (INT-800 harness contract, model-output
  injection seam, stub-logging).
- L3 beyond the two matrix slices (concurrency gate + peer delegation): the full
  4-turn cascade and its multi-hop causal-ordering assertion (flaky/marginal), and
  description-based routing (deferred pending a small SDK fix to surface descriptions
  in the passive roster - see the L3 section). Named routing / delegation /
  recruitment are already covered.
- Upper-bound assertions on model-driven content (only the narrow `assert_at_most`
  runaway-guard exception is allowed; see Decisions).
- Bridge adapters (a2a, a2a_gateway, acp), which the specs exclude from baseline
  status until defined separately.
