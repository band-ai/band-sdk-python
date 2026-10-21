---
name: band-investigate-test-error
description: Diagnose a concrete failing or flaky test when its root cause is unclear. Compare product, test-assumption, toolkit, and model hypotheses using empirical evidence. Use band-ci-triage to identify the failing GitHub Actions job first; use bug-hunting-via-example when the task starts from runnable examples rather than a failing test.
---

# Investigate Test Error

Read [the shared workflow runtime](../workflow-runtime.md) before execution.
Use its host-native tools, inherited model, capacity limits, sequential
fallback, and cleanup rules throughout this investigation.

Diagnose a failing or flaky test through **four competing investigation passes**, each
assigned one competing hypothesis for the root cause. Every investigator must
**validate** its hypothesis with evidence — reproduce, instrument, prototype, bisect
— not merely reason about it. You (the orchestrator) then scrutinize the reports,
push back on unsupported claims, issue follow-ups, and deliver a ranked conclusion.

## When to use

Start from an identified test and failure. For a CI status or job-log request,
first use `band-ci-triage`; for a named runnable example without a failing test,
use `bug-hunting-via-example`. Do not launch all three workflows for one symptom.

- A test fails or errors and the cause isn't immediately obvious from the traceback.
- A test is intermittently flaky and you need to isolate why.
- Before "fixing" a test, to confirm the fix targets the real fault (product code, the
  test's own assumptions, the test tooling, or the model) rather than masking it.

Do **not** use this for a trivial failure with an obvious one-line cause (a typo, an
import error, an off-by-one the traceback points straight at) — just fix those directly.

## Step 0 — Establish the ground truth first (do this yourself, before spawning)

Investigators reason better from a shared, reproduced baseline. Before delegating:

1. **Reproduce the failure** and capture the exact command, full output, and traceback.
   Run it 2–3 times if flakiness is suspected and record the pass/fail ratio.
2. **Identify the surface area**: which test file, which product/adapter code it
   exercises, which test tooling/fixtures/helpers it depends on, and whether an LLM is
   in the loop.
3. Write the reproduced error + command + surface-area notes into a short brief. Every
   investigator gets this brief so they don't re-derive it and so their findings are
   comparable.

The brief is the investigators' only shared context (they run as fresh agents — see
Step 1), so it **must be self-contained**. Include:

- The **exact repro command** (copy-pasteable) and any env it needs (e.g.
  `E2E_TESTS_ENABLED=true` plus required API keys).
- The **full traceback / failure output**, not a summary.
- The **flakiness measurement**: how many runs, how many failed, and whether it was run
  isolated vs. in-suite.
- **File paths** for the test, the product/adapter code it exercises, and the tooling it
  depends on — with line numbers where known.
- Any **environment specifics** that matter (branch, whether it reproduces on `main`).

If you cannot reproduce it at all, that itself is the finding — say so and treat
"non-reproducible / environment-specific" as the leading hypothesis.

## Step 1 — Assign the investigation passes

Use the host's available delegation tool, retaining its inherited model. Give
each investigator its hypothesis, the full brief, and the validation mandate
below. Start only as many independent investigators as available capacity
allows; queue the rest. If delegation is unavailable, perform all passes
sequentially and explicitly report that they were not independent reviews.

Prefer fresh context when the host supports it; otherwise provide the same
self-contained brief to inherited-context agents. Do not assume a particular
agent type, model name, or fork parameter exists.

Shared-checkout investigators are read-only and may run only experiments
that do not mutate source, Git state, or a shared environment. Any experiment
requiring instrumentation, dependency changes, or bisecting needs an isolated
checkout/environment preserving the failing baseline, or an exclusive
coordinator-run experiment after other readers stop. Preserve existing user
changes; remove only the experiment's own edits. Every report records the
revision and modifications its test actually exercised.

Assign exactly these four lenses, scoped to this SDK's code under `src/band/`, its tests,
and the test tooling they rely on. When no LLM is in the loop (most unit tests), drop
lens 4 and run three passes.

1. **Adapter / product logic bug.** Is the fault in the code under test — a real logical
   defect that requires a source fix? Look at the adapter(s) the test exercises (in
   `src/band/adapters/`, plus its CI-lane wiring for an E2E test), the matching converter
   in `src/band/converters/`, and the runtime in `src/band/runtime/`. Trace the code path
   the test drives and show the incorrect behavior.

2. **Test-case assumptions.** Is the test itself wrong — does it assume a flow, ordering,
   timing, or result that doesn't match the actual (and correct) system behavior? Are its
   assertions checking the right thing? Check timing assumptions against `AGENTS.md`
   "Test Synchronization". For an E2E test, scrutinize the matrix/baseline expectations
   (`tests/e2e/baseline/smoke/matrix/`), including agent-scoped `/context` recall
   assumptions and room rejoin/rehydration timing. Show the mismatch between what the test
   asserts and what the system correctly does.

3. **Test tooling / toolkit bug.** Is the fault in the harness the test relies on rather
   than in the product or the test's logic? Examine the suite's fixtures and helpers
   (`conftest.py`, `kit.py`), the shared `tests/conftest.py`, and for an E2E test the
   baseline toolkit (`tests/e2e/baseline/` — provisioning, user ops, reply capture, the
   LLM judge, assertions, fixtures). Show the toolkit misbehaving independently of the
   test's intent.

4. **LLM flakiness.** Is the failure driven by model non-determinism, or by
   weak/ambiguous prompts or judge criteria that a capable model can legitimately fail?
   Look at the adapter's system/`custom_section` prompt, the trigger message the test
   sends, and the judge rubric. Show variance across runs (per the flakiness measurement
   in the brief) and/or demonstrate that tightening the prompt changes the outcome.

### Validation mandate (put this in every investigator prompt)

> Do not conclude from reading and reasoning alone. **Validate your hypothesis
> empirically** and report the technique you used and its raw result. Acceptable
> techniques include: reproducing the failure, adding temporary instrumentation/logging
> in an isolated experiment, writing a standalone repro, bisecting an isolated checkout,
> running the test N times to measure flakiness, mocking out one layer to isolate it, or
> diffing expected vs actual behavior. State explicitly: what you tried, what you
> observed, whether the evidence **confirms or refutes** your assigned hypothesis, and
> your confidence (with the concrete evidence backing it). "I think it's probably X"
> without a validating experiment is not an acceptable finding — say "could not validate"
> instead. Follow the isolation rules above; clean up only your own experiment
> resources and edits. Report your findings; do not fix.

## Step 2 — Scrutinize the reports (your job, not the subagents')

When the investigators return, be a demanding reviewer, not a stenographer:

- Reject any claim not backed by a validating experiment. If an investigator "assumed",
  send a follow-up through the host's continuation tool (to the same agent) demanding
  the specific experiment that would confirm or refute it.
- Cross-check contradictions. Two lenses may both fire (e.g. a real adapter bug *and* a
  brittle assertion). Note overlaps and interactions.
- Issue follow-up tasks where evidence is thin, a repro is missing, or a promising lead
  was left dangling. Schedule targeted follow-up passes within available capacity.
- Watch for the common trap: a "flaky LLM" verdict that's actually a deterministic
  product bug or a too-strict judge. Demand the run-N-times evidence.

## Step 3 — Present the conclusion

Deliver a synthesis, not four pasted reports:

- **Ranked root cause(s)**: most-likely first, each with *what* is broken, *where*
  (file:line where possible), and the *evidence* that supports it.
- **Ruled out**: hypotheses the evidence refuted, and how they were refuted.
- **Recommended next step**: the specific fix or the next experiment if still ambiguous.
  Note whether the fix belongs in product code, the test, the toolkit, or the prompt —
  because that determines who owns it.
- Keep techniques visible: briefly cite how each surviving conclusion was validated so
  the user can trust it.

Do not apply fixes as part of this skill unless the user asks — the deliverable is the
diagnosis. Once the user picks a direction, implement it separately.
