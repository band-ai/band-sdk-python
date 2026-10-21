---
name: band-investigate-test-error
description: Use this skill when a test is failing or erroring and the root cause is not obvious — when the user asks to "investigate this test failure", "figure out why this test is flaky", "why is this test erroring", "find the root cause of this failure", or wants a rigorous multi-hypothesis diagnosis before touching code. Spawns parallel investigator subagents, each owning one hypothesis (adapter/product bug, test-case assumptions, test tooling/toolkit bug, LLM flakiness), makes each VALIDATE its hypothesis empirically rather than assume, then scrutinizes their reports and presents a ranked conclusion of what and where the issue is.
version: 1.0.0
---

# Investigate Test Error

Diagnose a failing or flaky test by running **four parallel investigators**, each
assigned one competing hypothesis for the root cause. Every investigator must
**validate** its hypothesis with evidence — reproduce, instrument, prototype, bisect
— not merely reason about it. You (the orchestrator) then scrutinize the reports,
push back on unsupported claims, issue follow-ups, and deliver a ranked conclusion.

## When to use

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

## Step 1 — Spawn four investigators in parallel

Send all four in a **single message** (multiple `Agent` tool calls) so they run
concurrently. Give each a prompt that (a) states its assigned hypothesis, (b) pastes in
the full self-contained brief from Step 0, and (c) enforces the **validation mandate**
below.

**Default to fresh agents, not forks.** Spawn each investigator as a fresh
`general-purpose` agent (omit `subagent_type` or set it explicitly) and, if you want to
save cost, pass a cheaper `model` (e.g. `"sonnet"`) — diagnosis work rarely needs the
top model. A fresh agent starts near-empty and carries only the brief you hand it, so
across four parallel agents you avoid re-paying this whole conversation's context four
times over (a `fork` inherits it all and is pinned to the parent model). This is exactly
why Step 0 produces a self-contained brief: it's the distilled context that makes fresh
agents viable.

Use `subagent_type: "fork"` **only** as an escape hatch — when the investigation needs
nuance that lives in *this* session's transcript (e.g. you've already done extensive live
debugging here) and the brief genuinely can't capture it. Otherwise a fresh agent is
cheaper and more focused.

Assign exactly these four lenses, scoped to this SDK's code under `src/band/`, its tests,
and the test tooling they rely on. When no LLM is in the loop (most unit tests), drop
lens 4 and spawn three.

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
> techniques include: reproducing the failure, adding temporary instrumentation/logging,
> writing a minimal prototype or standalone repro, bisecting (git or by disabling parts),
> running the test N times to measure flakiness, mocking out one layer to isolate it, or
> diffing expected vs actual behavior. State explicitly: what you tried, what you
> observed, whether the evidence **confirms or refutes** your assigned hypothesis, and
> your confidence (with the concrete evidence backing it). "I think it's probably X"
> without a validating experiment is not an acceptable finding — say "could not validate"
> instead. Revert any temporary edits you make. Report your findings; do not fix.

## Step 2 — Scrutinize the reports (your job, not the subagents')

When the investigators return, be a demanding reviewer, not a stenographer:

- Reject any claim not backed by a validating experiment. If an investigator "assumed",
  send a follow-up via `SendMessage` (to that agent, so it keeps its context) demanding
  the specific experiment that would confirm or refute it.
- Cross-check contradictions. Two lenses may both fire (e.g. a real adapter bug *and* a
  brittle assertion). Note overlaps and interactions.
- Issue follow-up tasks where evidence is thin, a repro is missing, or a promising lead
  was left dangling. It's fine to spawn a targeted fifth investigation.
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