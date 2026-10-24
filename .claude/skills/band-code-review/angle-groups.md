# The six angle-groups

Shared by `band-code-review` and `band-pr-triage`. An agent reads the intro and
only its own group's section, plus any guideline skill that section names
(`.claude/skills/<name>/SKILL.md`), which owns that rule.

Every finding has exactly one owning group. Test code belongs to Test Quality
alone, which applies every angle to it; other groups review non-test code. A
subagent applies only its own angles and drops anything belonging to another.
Different groups may return distinct findings on the same line when reasons
and consequences differ.

**A — Style & Hygiene**
- DRY, single source of truth, no magic numbers or strings
  (`band-python-conventions`): a repeated comparison or shape counts too; if
  no constant or helper exists, the fix creates one.
- Comments (`band-python-conventions`, "Comment Style").
- Files that mix unrelated concerns (`band-python-conventions`).

**B — Design & Reuse**
- Idiomatic constructs (`band-python-conventions`); weigh real tradeoffs
  (recursion on deep input is a regression).
- Reuse of an existing helper over new logic (`band-python-conventions`). No
  claiming duplication or its absence without grepping the codebase.
- Ground any library/API behavior in current docs, source, or a sibling
  repo's usage; a plausible name or signature is not evidence. Prefer an
  established third-party tool over hand-rolling (AGENTS.md "External
  Research & Code Reuse").

**C — Runtime Safety & Observability**
- Concurrency and memory safety: unsynchronized shared state, check-then-act
  races, unsafe memory access. Flag the pattern with the concrete
  interleaving that triggers it. A claimed wrong outcome of a multi-step flow
  belongs to Edge-Case Flow Analysis, where it gets proven.
- Logging (`band-python-conventions`): flag failure paths or non-obvious
  branches with none, and logs that are noisy or carry PII (identifiers,
  contents, secrets).

**D — Test Quality**
Test code belongs here alone. Read the whole `band-python-test-conventions`
skill, not only "Coverage is branches, not lines". Apply A–C more strictly
than on production code (SSoT, intent-named helpers, no mixed-concern files,
no magic wire strings). Each of the following is an `issue:`, not a nit:

- Coverage of complementary branches, not line counts. Map each changed
  `match`/`if`/timeout/OS decision to the test that proves each reachable
  outcome. Proving fail-closed does not prove the success side; a
  `skipif(os.name)` is a missing branch on the skipped OS. Name the
  regression each proposed test would catch. Passing tests or high line
  coverage alone are not evidence.
- Assertions of plumbing or assumptions: occupancy arithmetic, private
  fields, mock call counts, event-type tallies as a proxy, empty collections
  the test does not own, or tautologies. The assertion must be a named
  projection of the decision.
- A test function that branches on scenario/kind (`if scenario != ...`) or
  re-types a wire literal the peer or product already names. Parametrize
  `(scenario, expected)` so each row *is* the claim.
- The interaction under test is patched away (`unittest.mock.patch` of
  private names, stdlib spawn, or timeout constants) instead of a public
  seam or a real peer. `AsyncMock` of the unit under test is the same smell.
- Wall-clock sleep or waiting out a grace period used as proof.
  `asyncio.timeout` is a hung-test guard only; a public timeout of `0` is
  "check now", and it still needs the complementary "finished before wait"
  sequence.
- A skipped, disabled, or silently-ignored failing test.
- A Hypothesis model or kit file that looks like a collected test (a `Test*`
  class, or a missing role docstring, in a non-`test_*.py` module).

**E — Edge-Case Flow Analysis**
Owns failures that need a *sequence* of state transitions or interactions. A
strange input alone isn't one, and a defect in a single local decision belongs
to Logical Bugs. The **mapper** writes state down before hunting, limited to
components the diff touches:
1. **Invariants** — promises the changed flows must keep, from the ticket,
   plan, PR description, tests, and docstrings. Never invent behavior.
2. **State table** — states × events (repeat, retry, cancel, disconnect,
   another actor's in-progress change); mark each cell handled, rejected, or
   impossible. Unmarked cells are candidates.
3. **Side-effect gaps** — list each flow's side effects in order; at each gap
   ask what happens on failure, double run, retry after timeout, or
   interrupt and resume.

The mapper returns hypotheses (start state, triggering sequence, the real
source that can produce it, the invariant broken) and a one-line-per-flow
**coverage map** (`clean`, `hypothesis`, or `not examined`). The shared
skeptic proves or drops each hypothesis. Where the component has a simple
model of expected behavior, a Hypothesis `RuleBasedStateMachine` is the
preferred proof (`band-python-test-conventions` names the repo's pattern):
it also finds missed sequences and shrinks failures.
Surviving findings carry their flow in `fix_note`.

**F — Logical Bugs**
- The **explorer** maps changed local decision points (calculations,
  conditionals, boundaries, callers, existing tests) and returns only
  concrete hypotheses with the input/state and path that should expose each.
- One **skeptic** takes hypotheses from both the explorer and the Edge-Case
  mapper, loading PR-head context once. For each it independently
  reconstructs the path from source and proves the failure with a repro
  script or a test that fails on current code. Reasoning alone isn't enough;
  if it can't be cheaply proven, drop it. Every drop comes back with its
  reason and footprint for a no-fix record ([no-fix-records.md](no-fix-records.md)).
- It also proves **reachability**: the repro drives real public entry points,
  or the proof cites the production caller chain or event source that
  produces the start state and sequence. Failures that need poking private
  state, mocking away a guard, or events no real source emits are dropped.
- A proven, reachable defect outside the scope ([pr-target-scope.md](pr-target-scope.md)) is
  not dropped: it comes back marked `out_of_scope`, with why. Only claims
  that aren't real are dropped.
- Skeptic-confirmed findings (E and F) ship with their proof and are always
  `issue:` — only confirmed defects, never nits or suggestions.

Security review is a separate task: use [band-security-review](../band-security-review/SKILL.md)
when requested. Do not imply these six angles establish security coverage.
