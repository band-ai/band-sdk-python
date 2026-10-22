# Plan content and scope

Write the draft to the plan file. If Phase 0 kept a prior plan, start from it:
keep what Phase 1 still confirms, fix or drop what's stale, extend what it
missed, and drop its Implementation Prompt — Phase 8 writes a new one from
the final plan. Where a decision still waits on the user (Phases 3 and 4), the draft
uses the option you recommend and lists the other under Alternatives
considered; Phase 7 asks.

Sections, in order, each short; "n/a" is fine where one doesn't apply:

1. **Problem:** the root cause in one sentence, the chosen level (local fix or
   architectural adjustment), and why.
2. **Goals and non-goals:** what the change must achieve, and reasonable
   things it deliberately won't do, including deferred followups.
3. **Scenarios:** concrete "given / when / then" lines for the main flow, the
   edge and failure cases, and nearby behavior that must stay unchanged. Each
   names who or what triggers it (a user in a room, an adapter, the platform,
   a CLI call) and what they observe, not which function runs.
4. **Approach:** the design, the files it touches, and the outside option
   chosen in Phase 4 with the sources it rests on. A library capability that
   only running it can confirm is stated here as unconfirmed, with the call
   that confirms it.
5. **Alternatives considered:** doing nothing, the symptom patch the ticket
   implies, and one or two real alternatives, each with the reason it lost.
6. **Compatibility:** every public symbol, config field, default, and wire
   shape the change touches; whether each change breaks callers, the semver
   impact, and the deprecation path.
7. **Cross-cutting:** one line each for security, logging and observability,
   paid-LLM cost, concurrency and ordering, and Windows/Ubuntu behavior.
8. **Test plan:** at least one test per scenario, at the lowest level that
   really exercises it.
9. **Definition of done:** automated checks as exact commands (the targeted
   `uv run --all-packages pytest ...` and applicable checks from `AGENTS.md`'s
   Pre-Commit Checklist) and relevant manual checks (live E2E, editor flows),
   each with its expected result.
   Include the draft-first PR lifecycle and author readiness review from
   `AGENTS.md`; approval of this plan is not proof that implementation or
   testing is complete.

Design rules:

- The smallest elegant solution that removes the root cause, at the level
  Phase 3 chose. No speculative abstraction, no designing for a hypothetical
  future ticket.
- DRY: one named constant, enum, or helper per concept; no magic values.
- Fits the surrounding code's idiom and the `band-python-conventions` skill,
  so the diff reads like it was always there.

Test rules:

- Test behavior that can actually break, not defaults or implementation
  trivia (see the `band-python-test-conventions` skill). A test that would
  still pass under a symptom-level patch doesn't cover the root cause.
  Cover complementary branches of each owned decision, not line execution;
  assert a named projection of the outcome, not internals or unclaimed
  empties. Parametrize `(scenario, expected)` — do not `if` on scenario
  inside the test.
- When a material scenario needs end-to-end coverage to prove the behavior,
  reuse or extend the `tests/e2e/baseline` toolkit (see the
  `band-write-e2e-test` skill) instead of building new plumbing. Otherwise use
  the lowest boundary that preserves the behavior being tested.
- A missing helper, fixture, constructor seam, or local peer is its own
  step, before the step that uses it — never `unittest.mock.patch` of a
  private name, stdlib spawn, or timeout constant, and never an empty mock
  in place of a real boundary. Timeouts are proven with a public `0` ("check
  now") plus the complementary "finished before wait" sequence, not by
  sleeping the grace period.

Scope rules:

- Everything traces back to the ticket's problem, its acceptance criteria, or
  the root cause behind them.
- Out-of-scope but important: small (localized, no design or API change) →
  fold in and flag as an in-passing extra; large → a non-goal and a candidate
  followup for Phase 6.
