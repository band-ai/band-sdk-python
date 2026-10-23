---
name: band-python-test-conventions
description: Band Python SDK test conventions and coverage assessment. Use when writing or reviewing tests, assessing whether changed behavior is adequately covered, or checking that tests validate meaningful outcomes and realistic complex flows. Also use when a test asserts internals, patches private names, waits on wall-clock grace, branches on scenario inside the test, or reports line coverage instead of complementary branches.
paths:
  - "tests/**/*.py"
  - "src/**/*.py"
---

# Test Conventions

`band-python-conventions` still applies: single source of truth, intent-named
helpers, and no mixed-concern files. This skill is the extra bar for tests.

## Coverage is branches, not lines

Assess the relevant existing tests whenever behavior changes or coverage is
reviewed, even if the diff adds no tests. A green suite, a test count, or a
line-coverage percentage alone does not establish adequate coverage.

Map each production decision the change owns — a `match`/`case`, an `if` that
chooses an outcome, an OS spawn path, a timeout pending-vs-done wait — onto
the tests that prove it. For every such decision:

1. Name the reachable outcomes (succeed, fail-closed, ignore, retry, OS-A vs
   OS-B).
2. Name the test that proves each as an observable sequence, not as a line
   that executed.
3. Treat complementary pairs as separate scenarios. Proving one side does not
   prove the other: backlog live-at-limit vs overflow; a callback that
   finishes before the wait vs one still running; settlement before the
   matching result vs result before settlement; POSIX process-group vs
   Windows job.
4. Report remaining unexamined outcomes explicitly. A Hypothesis model covers
   generated sequences of its Step alphabet, not branches those steps never
   take.

Prioritize consequential behavior, changed branches, and regressions over a
numeric target. Do not add every case to every change; select scenarios the
actual feature can reach.

For each test, name the plausible bug it would catch. Assert the observable
outcome and relevant side effects; a test that only checks that a mock was
called or that nothing raised may leave the real behavior unverified.

Exercise complex flows as realistic sequences, not just isolated steps.
Where relevant, cover follow-up turns, failure and recovery, cancellation,
restart/rehydration, concurrent rooms or agents, permissions, and cleanup.

Extend useful existing tests before duplicating them. Follow `AGENTS.md` for
synchronization and validation scope, without silently expanding the task or
launching unrelated suites.

## Assertions show intent, not plumbing or assumptions

Tests must be declarative and intent-revealing, not a transcript of the
implementation. Assert on a readable projection of the observable outcome —
the thing the test is actually about — never on raw internals or on a side
effect that merely implies the real answer.

- `assert reply.outline == ["tool_call (permission)", "message", ...]` over a
  hand-rolled comprehension pulling `message_type` out of each event dict.
- `assert record.levelno == logging.DEBUG` over inferring a log level
  indirectly from whether two separate capture windows came back empty.
- A named helper (`owned_rpc_process`, `outline`) over poking `_proc` or
  counting `AGENT_END` as a proxy for "the turn finished twice".
- Overload of `EVENT_BACKLOG_LIMIT + 1` over occupancy arithmetic
  (`limit - 2`) that restates the queue's internals.

If writing the assertion requires re-deriving *how* the code decided
something, the test is checking the wrong thing — assert the decision
itself.

Do not assert an assumption the test does not own. Drop empty-collection
checks on a mapping test that is about session keys, tautologies such as
Windows `(None, None)`, and "observer stayed empty" when the claim is
fail-closed-and-restarted. If the assertion would still pass after inverting
the behavior on an unclaimed axis, it is noise, not coverage.

Test what really matters — behavior that can break. Don't write tests that
restate definitions (asserting dataclass defaults equal themselves, echoing a
constant) or otherwise cannot fail for a real reason.

Decide what correct looks like before running the code, then assert that;
never bend an assertion to match whatever the code currently produces.

## One concern per test, no in-test branching

Each `test_*.py` holds exactly one `class Test...` named for the behavior it
covers — no module-level test functions and no helper classes beside it.
When a suite outgrows one file, make it a package: `conftest.py` for
fixtures, a plain helper module (`kit.py`, a Hypothesis `lifecycle.py`) for
shared doubles, and one test file per concern.

A helper module in that package is not a collected test. Its module
docstring names the role (kit, peer, lifecycle model). It does not define a
`Test*` class. The collected test that *plays* a Hypothesis model lives in
`test_lifecycle_model.py` (see `tests/platform/linkclose/test_lifecycle_model.py`).

One test function, one claim. Parametrize `(scenario, error)` so each row's
assertion *is* the claim. A long test that dispatches with `if scenario !=
"aborted"` or `if os.name == "nt": return` is several tests hiding in one
function; split them.

Wire statuses, prompt texts, and error messages belong to one named constant
or enum that the peer, the product, and the assertion all reference. A
hard-coded `"complete"` / `"Approve"` in the test is a second copy of a
closed vocabulary.

## Drive the real boundary; inject, don't patch

Drive the real entry point and interacting components at the lowest level
that preserves the behavior under test. Use realistic peers or fakes at
external boundaries; do not mock away the interaction whose correctness the
test claims to prove.

- A constructor seam (`create_process=`) or a public instance timeout of `0`
  ("check now / no grace") is the test hook. `unittest.mock.patch` of
  `asyncio.create_subprocess_exec`, a private `_close_process`, or a
  `_CALLBACK_EXIT_TIMEOUT_S` constant is not.
- `AsyncMock` is a double at an external boundary (a client the function
  *takes*), not a stand-in for the unit under test. A receipt-table test may
  mock the RPC client; it then does not count as coverage of the client's
  prompt path.
- Use the baseline toolkit for needed live E2E coverage, not as a mandatory
  layer for every test.

## Time is a hung-test guard, not proof

Ordering, concurrency, and timeout behavior is proven with explicit
synchronization and a controlled clock (`AGENTS.md` "Test Synchronization"):
events, gates, barriers, or observable state transitions. Never use elapsed
wall time, `asyncio.sleep` of a grace period, or "it finished within a
duration" as proof.

- `async with asyncio.timeout(N)` around a test is a hung-test guard. It
  must not be the mechanism that establishes the outcome.
- To prove a timeout fail-closed path, set the public timeout to `0` and
  assert the failure. Do not wait out the production grace period.
- `timeout = 0` proves "still pending at the wait". It does not prove the
  complementary branch: work that finishes before the wait succeeds. Cover
  that with a sequence that completes, not with a longer sleep.

Real subprocess and network readiness stay independent of a controlled
clock. Cleanup must account for resources created during failed or cancelled
startup and await their confirmed release.

## Tests run on Ubuntu and Windows CI

Test across OSes; don't skip one. Build host paths with `tmp_path` or
`tests.paths.host_absolute_path`, never a hard-coded `"/opt/..."` (no drive,
so relative on Windows); check absoluteness with
`band.workspaces.is_host_absolute`.

Where behavior differs per OS, write one parametrize table that runs
everywhere, each row naming the `os.name` values that accept it.
`skipif(os.name ...)` is only for a boundary that cannot run on that OS,
never for a path, separator, or "this assertion is awkward on Windows". A
skip is a missing branch on the skipped OS.

## Paths, bugs, skips

- In tests, never derive repository-anchored paths with per-file
  `Path(__file__).parents[N]` arithmetic — import the anchors from
  `tests/paths.py` (`REPO_ROOT`, `SRC_ROOT`, `EXAMPLES_ROOT`, `KIT_DIR`,
  `ENV_TEST_FILE`). Only genuinely package-relative paths (a fixture file
  next to its test) stay relative to their own `__file__`.
- A skipped, disabled, or silently ignored failing test is always a blocking
  issue.
- A fix for a confirmed bug comes with a test that fails before the fix and
  passes after it.
- For a stateful lifecycle component, add a Hypothesis model-based test over
  generated step sequences.
