---
name: band-write-e2e-test
description: Use this skill when the user asks to write, add, or extend an end-to-end (E2E) test for this SDK — "write an e2e test for X", "add a baseline smoke for adapter Y", "we need an E2E test covering Z behaviour", "cover this flow in tests/e2e". Runs a disciplined pipeline — parallel context collection (docs + toolkit map + placement), an explicit plan, a plan review gate (reuse? elegant? meaningful? already covered?), implementation, a focused plan/toolkit review plus automated code review, and a run-and-fix loop that defers to band-investigate-test-error on failure. Its north star — test the real behaviour with the existing baseline toolkit — no reinvented plumbing, no over-mocking, no assertions bent to match whatever the code happens to do.
---

# Write an E2E Test

Read [the shared workflow runtime](../workflow-runtime.md) before execution.
Use host-native delegation within available capacity; when unavailable, run
the same research/review passes sequentially and report the self-review.

Produce a live end-to-end test that exercises **real** platform + adapter behaviour,
built entirely from the existing baseline toolkit. The pipeline is: collect context in
parallel → plan → **gate the plan** → implement → review → run → fix elegantly. Do not
skip the gate: most bad E2E tests are bad because they reinvent plumbing, over-mock, or
assert whatever the code already does. This skill exists to catch that before code is
written.

## North star (read before anything)

- **The test is the scenario, never the plumbing.** Provisioning, waiting, adapter
  construction, and assertions all already exist in `tests/e2e/baseline/`. Reuse them.
- **Test the real thing.** No over-mocking, no assertions written to satisfy the current
  output, no adapting the assert to whatever the run produced. Decide what *correct*
  looks like first, then assert that.
- **Fail loudly, never silently.** A missing key/CLI/server *fails* with a reason; only
  `E2E_TESTS_ENABLED` skips. Agents are non-deterministic — assert *behaviour held* (a
  floor, a substring, a metadata fact, an injected marker), never exact counts, strict
  ordering, or literal transcripts.
- **Pick the barrier by what you assert.** To assert on **reply text**, use
  `replies = await capture.wait_for_reply(mid, agent_id[, since=])` and assert
  on its return value. To read **durable** turn state (tool calls, usage, events, memory)
  or when a reply is optional, use `await capture.wait_for_processed(mid, agent_id)`.
  Never `wait_for_processed` and then assert on `capture.messages`: the reply
  (`message_created`) and the delivery-status (`message_updated`→PROCESSED) are
  independent, unordered platform events, so PROCESSED can land before the reply is
  buffered — reading the buffer there is a cross-adapter race ("no agent messages were
  captured"). `wait_for_reply` closes it by waiting for the reply frame itself.

## When to use

- The user wants a new E2E/baseline test, or to extend the matrix to cover a behaviour.
- A behaviour is only covered for one adapter and should run across frameworks.

Do **not** use this for unit/adapter/converter tests (those live in `tests/adapters/`,
`tests/converters/`, `tests/framework_conformance/` and don't touch the platform), or for
a one-line tweak to an existing E2E test — just make it.

## Phase 1 — Collect context

Assign these three read-only research passes using the shared runtime's
delegation rules. Give each a self-contained brief and request a tight summary,
not file dumps. Batch independent passes within available capacity; otherwise
perform them sequentially.

1. **Docs sweep.** "Read `tests/e2e/baseline/README.md` and
   `tests/e2e/baseline/ADDING_AN_ADAPTER.md` in full, plus the `AGENTS.md`
   "Testing Structure" and "Environment Variables" sections. Return: the toolkit's rules, the `@with_adapters` vs `@per_adapter` decision,
   the assertion strategy (cheap-first, judge-last), the fail-loud/validation policy, the
   CI-lane model, and any env vars a test needs. Quote the exact decorator/fixture names."

2. **Toolkit map.** "Map every reusable fixture, helper, decorator, sample tool,
   sample-agent shape, and assertion available under `tests/e2e/baseline/` (walk
   `toolkit/`, `agents.py`, `fixtures/`, `smoke/samples/`, `toolkit/observations/`).
   Return a table of name → what it does → where, so the caller can reuse instead of
   rebuild. Flag anything close to a provisioning/waiting/assertion helper the caller
   might otherwise hand-roll."

3. **Placement + precedent.** "Given the behaviour `<describe it>`, find where a test for
   it belongs (`smoke/matrix/`, `smoke/behavior/`, `smoke/inspection/`, or
   `smoke/adapters/`), and the 2–3 most similar existing tests to mirror for
   structure and style. Return the target file/path, the closest precedents with
   file:line, and **whether the behaviour already appears to be covered** (an existing
   test to extend/parametrize rather than duplicate)."

After all three passes, synthesize a short context brief (toolkit pieces to
reuse, target location, precedents, and any coverage overlap).

## Phase 2 — Plan the test

Apply [test conventions](../band-python-test-conventions/SKILL.md)
"Coverage is branches, not lines" when mapping existing coverage and selecting
the scenario. Show which complementary-branch gap this test closes and which
complete interaction it proves.

Write a concrete plan (not code yet). It must answer:

1. **What real thing are we testing?** State the behaviour and what *correct* looks like,
   independent of the current implementation. Name the failure it would catch. No
   over-mocking; if you're mocking the thing under test, stop and rethink. If the
   behaviour spans turns (pause/resume, ask-then-answer, rehydration), plan the **full
   conversational arc** — real bugs often first surface on the *second* interaction, so
   a single round-trip smoke can stay green while the feature is broken.
2. **What kind of test is it?** Pick the topology deliberately (from the README's table):
   - one scenario across the matrix → `@per_adapter()` → `agent`
   - a subset / capability filter → `@per_adapter(..., supports=/exclude=/runs_tool_loop=)`
   - specific named agents interacting in one room → `@with_adapters(A, B)` → `agents`
   - self-driven lifecycle (build-only, reboot, rehydration) → `@per_adapter()` → `cell`
   - a cross-framework author + rehydrator →
     `@per_adapter(exclude=[ExcludedAdapter(X, "reason")], peer=X)` → `cell` + `peer`
   Note the setup each needs (room participants, prompt/features shape, custom tools,
   `**TOOL_AGENT`/`**MEMORY_AGENT`/`**EXECUTION_REPORTING`).
3. **What do we assert, and how cheaply?** Prefer a structural check on
   `capture.messages`/`tool_calls`/`memory`/events (`assert_contains_any`,
   `assert_fired`, `assert_stored`, …). Reserve `judge` for genuinely semantic outcomes.
   Every assertion must follow the barrier (`wait_for_reply` / `wait_for_processed`) —
   never a `sleep`.
   Make markers **provenant**: a high-entropy token injected at exactly one point in
   the flow (a handler's return value, a user's mid-conversation answer) proves the
   round trip only because nothing else — the model included — could have supplied it.
   And know your capture filter: verify what the listener actually admits (sender/
   message-type filters, min-message counts, how rounds are separated) and that the
   feature's output shape matches it — an assertion on messages the capture never
   admits is vacuous or flaky.
4. **Lane sanity.** If the test touches more than one framework, confirm they share a home
   lane or plan the `@lane(Lane.X)` pin — otherwise it fails collection.

Known traps: agent-scoped `/context` recall (address the fact *to the agent*),
LangGraph's default model intermittently skipping a driven tool call
(`band_store_memory`, `band_send_event`), heterogeneous agents must use
`@with_adapters` within one lane, and don't preemptively add flaky reruns. Suites that
reuse one shared agent id are coupled through host-global SDK state (instance locks,
on-disk sessions): every test must fully stop its agent, and two concurrent pytest
processes on the same id fail loudly at startup by design.

## Phase 3 — Review the plan (gate — do not write code until this passes)

Answer all five honestly. If any is "no", fix the plan and re-ask:

1. **Reuse, not reinvention?** Every piece of plumbing maps to an existing fixture/helper
   from Phase 1. Nothing hand-rolled that the toolkit already provides.
2. **Simple and elegant?** The body reads as "send this, expect that". Plumbing that
   crept in belongs in the toolkit, not the test.
3. **Testing the real thing?** Asserting correct behaviour, not the current output; not
   mocking the subject; not a tautology.
4. **Meaningful?** It would go red on a real regression and green otherwise. Name that
   regression.
5. **Not already covered?** Phase 1 found no test that already asserts this. If one nearly
   does, **extend/parametrize it** instead of adding a parallel test.

State the five answers explicitly before proceeding — this is the gate.

## Phase 4 — Implement, then review for plan & toolkit conformance

Write the test yourself (you are the implementing engineer), guided by the plan and the
nearest precedent, reusing the toolkit exactly. Keep it minimal and consistent with
sibling tests. If a genuinely new reusable piece is needed, add it to the shared module
(a `ToolSpec` in `sample_tools`, an instruction in `sample_agents`, an assertion on the
collection) — never inline a duplicate. When an existing helper already does the right
dance but discards data the test needs (e.g. returns nothing instead of the captured
messages), extend it backward-compatibly and call it — don't inline its body into the
test.

Then perform a focused plan/toolkit review, independently when delegation is
available. Supply a self-contained brief (plan, diff, toolkit rules) and keep
the reviewer read-only. A sequential self-review must be reported as such.
This pass verifies:

- **Matches the plan & topology** — the chosen `@per_adapter`/`@with_adapters` shape,
  fixtures, and assertions are the ones the plan specified.
- **Toolkit invariants hold** — barrier before assert, capture opened before send,
  `snapshot()`/`since()` on reused captures, no `sleep`, no exact-count/ordering asserts,
  the `Adapter` enum (no magic strings / hardcoded lists), fail-loud not skip, and
  lane-schedulability.

Generic elegance, simplification, and correctness are **not** this agent's job —
Phase 5 covers them, so don't duplicate. Coverage/duplication was settled at the Phase 3
gate; don't relitigate it here. Scrutinize the report (don't stenograph): reject
unsupported claims and fold the surviving findings back into the test.

## Phase 5 — Local diff review

Review the diff using [the project review angles](../band-code-review/angle-groups.md)
and an available `simplify` skill, then address verified findings. Combine
this correctness/reuse pass with Phase 4's plan/toolkit checks. This review
does not post to GitHub; invoke `band-code-review` only when explicitly
requested and an open PR exists.

## Phase 6 — Run, and fix elegantly on failure

Run just this test (scope narrowly; `--no-cov`; live suite needs the env):

```bash
E2E_TESTS_ENABLED=true uv run --all-packages pytest '<collected-node-id>' -v -s --no-cov
```

Use `--collect-only -q` on the target file to obtain its exact node id,
including the test class and matrix parameter where present. Follow the
project's one-class-per-file convention for new or touched tests. Set
`BAND_E2E_LANE=<lane>` and select the lane's `uv` extra per the README; use
the isolated commands from `band-run-e2e-live` for concurrent lanes.

Budget the timeout; never let reruns absorb one. A test with multiple runtime boots or
agent lifecycles needs an explicit `@pytest.mark.timeout(extra=n)` above the conftest
default — a mid-test timeout is a **signal kill** that skips async unwinding and can
poison the whole pytest process (leaked host-global locks, zombie agents still running
on the session loop). Between-attempt cleanup belongs in an autouse fixture (reruns re-run
function fixtures); reap by **stopping the leaked owner**, not by freeing its resource
under a still-live zombie. When reruns are justified, classify them with
`flaky_model(reason)` or `flaky_infra(reason)` from `tests/e2e/baseline/flaky.py` — a
raw `@pytest.mark.flaky` fails collection.

- **On pass:** report the result plainly (command + outcome). A passing test
  alone does not make a PR ready. When publishing is part of the task, follow
  `AGENTS.md` "PR Lifecycle and Readiness Review" before promoting its draft.
- **On failure or flake:** invoke the **`band-investigate-test-error`** skill to find the true
  root cause before touching anything — the fault may be product code, the test's
  assumptions, the toolkit, or the model, and each has a different owner.
  - **Fix elegantly — no patches, no bolt-ons.** Once the root cause is known, design a
    clean fix (product code, test, toolkit, or prompt — wherever it belongs). Do not add
    reruns, sleeps, or loosened asserts to paper over a real defect. Do not bend the
    assertion to match a wrong output.
  - **Re-review the fix** (re-run the relevant Phase 4 and Phase 5 passes), then
    re-run the test.

## Hard rules (never violate)

- ❌ hand-rolled provisioning/waiting/adapters/assertions where a fixture exists
- ❌ `time.sleep`/`asyncio.sleep`/fixed silence windows — use the event-driven waiters
- ❌ exact-count / strict-ordering / literal-transcript / mandatory-silence asserts
- ❌ magic-string adapter ids — use the `Adapter` enum; ❌ hardcoded adapter lists
- ❌ skipping on missing config — it must **fail** with the reason
- ❌ the `judge` by reflex — cheapest structural check first
- ❌ asserting whatever the code currently outputs instead of what's correct
