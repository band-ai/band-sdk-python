---
name: band-python-test-conventions
description: Band Python SDK test conventions and coverage assessment. Use when writing, extending, or reviewing any test (unit, adapter, converter, conformance, runtime, or E2E), assessing whether changed behavior is adequately covered, or checking that tests validate meaningful outcomes and realistic complex flows. Also use when a test asserts internals, patches private names, sleeps, depends on run order, uses unittest.TestCase, branches on scenario inside the test, or reports line coverage instead of complementary branches.
paths:
  - "tests/**/*.py"
  - "src/**/*.py"
---

# Test Conventions

`band-python-conventions` still applies: single source of truth, intent-named
helpers, and no mixed-concern files. This skill is the extra bar for tests.
Live E2E authoring follows `band-write-e2e-test` on top of this skill.

Rules marked **(non-live)** apply to tests that run in the CI unit job
(everything outside `tests/e2e/` and `tests/integration/`). All other rules
apply to every test.

## Before writing

Do this before the first line of test code:

1. **Name the decisions** the change owns and each one's reachable outcomes,
   including the complementary pairs ([coverage](#coverage-is-branches-not-lines)).
2. **Find what exists.** Search for a test that already covers the behavior
   and extend or parametrize it rather than duplicating it. Reuse the nearest
   `conftest.py` fixtures, the package `kit.py` doubles and peers, the shipped
   fakes in `band.testing` (`FakeAgentTools`), the event factories in
   `tests/conftest.py` (`make_message_event`, ...), the waits in
   `tests/runtime/conftest.py` (`wait_for_condition`, `Waiting`), and the
   shared modules in `tests/` (`paths.py`, `ports.py`, `identifiers.py`, ...).
3. **Pick the boundary.** Decide which real components the test drives and
   where the single double sits ([real boundary](#drive-the-real-boundary-inject-dont-patch)).
4. **Decide what correct looks like** before running the code.

## Before finishing

Re-read the test diff against this list. Report each item as held or not
applicable; fix what does not hold.

- [ ] Every claimed decision outcome maps to a named test; unexamined outcomes
      are reported ([coverage](#coverage-is-branches-not-lines)).
- [ ] Assertions compare the whole observable outcome with plain `assert`; no
      `TestCase`, no `self.assert*`, no popped or filtered fields
      ([assertions](#assertions-show-intent-not-plumbing-or-assumptions)).
- [ ] One `class Test...` per file, one claim per test, no in-test branching;
      variations use `@pytest.mark.parametrize`
      ([one concern](#one-concern-per-test-no-in-test-branching)).
- [ ] Repo-owned behavior under test runs for real; doubles sit at external
      boundaries, and no `patch`/`monkeypatch.setattr` targets a repo-owned
      name except a documented nondeterminism pin
      ([real boundary](#drive-the-real-boundary-inject-dont-patch)).
- [ ] No `time.sleep`, no grace-period `asyncio.sleep`; deadlines use
      `looptime` ([time](#time-is-a-hung-test-guard-not-proof)).
- [ ] **(non-live)** Passes under xdist and any `pytest-randomly` order; no
      shared mutable state, no live network, every started task awaited or
      cancelled ([isolation](#isolation-non-live)).
- [ ] Runs on Ubuntu and Windows without an OS skip
      ([OS](#tests-run-on-ubuntu-and-windows-ci)).
- [ ] Paths come from `tests/paths.py`; a bug fix has a test that fails
      before it ([paths, bugs, skips](#paths-bugs-skips)).

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

Use plain `assert actual == expected` so pytest's rewriting shows the diff.
Never subclass `unittest.TestCase` or call `self.assert*`; `unittest.mock`
doubles at external boundaries remain fine.

Compare the whole expected value, not a cleaned-up copy. Do not pop,
extract, or skip unpredictable fields before comparing. When the test
already controls the value (a fake peer, a fixture, `looptime`), make the
expectation exact. Otherwise put `mock.ANY` on that one field inside the
full comparison and keep every other field exact. Do not add product seams
only to make a value predictable.

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
The module docstring states that behavior in one line, and each test name
reads as its claim (`test_direct_turn_reports_named_activity`).
When a suite outgrows one file, make it a package: `conftest.py` for
fixtures, a plain helper module (`kit.py`, a Hypothesis `lifecycle.py`) for
shared doubles, and one test file per concern. Keep domain fixtures in the
package's own `conftest.py`; the root `tests/conftest.py` holds only what
the whole suite shares. A test package's `__init__.py` is empty or holds only
a docstring: shared types, probes and helpers go in a named helper module
(`kit.py`, `probe.py`), never in `__init__.py`.

A helper module in that package is not a collected test. Its module
docstring names the role (kit, peer, lifecycle model). It does not define a
`Test*` class. The collected test that *plays* a Hypothesis model lives in
`test_lifecycle_model.py` (see `tests/platform/linkclose/test_lifecycle_model.py`).

Set up and tear down with fixtures and context managers (`yield` fixtures,
`async with`), not `setUp`/`tearDown` methods. Express intent with markers
and decorators; every marker must be registered in `pyproject.toml`
(`--strict-markers`).

One test function, one claim. Parametrize `(scenario, error)` so each row's
assertion *is* the claim; use `@pytest.mark.parametrize` rather than
duplicate tests or a loop over cases inside one test. A long test that
dispatches with `if scenario != "aborted"` or `if os.name == "nt": return`
is several tests hiding in one function; split them.

Use a compound flow when the interaction between its phases is the claim
(for example, cancellation → retirement → a clean follow-up). Keep those phases
visible in the test. Split independent invariants; extract resource lifecycle,
not scenario actions and assertions, to shorten setup.

Wire statuses, prompt texts, and error messages belong to one named constant
or enum that the peer, the product, and the assertion all reference. A
hard-coded `"complete"` / `"Approve"` in the test is a second copy of a
closed vocabulary. Shared input tables (such as `tests/identifiers.py`) and
Hypothesis strategies, not ad hoc literals, supply varied input data.

## Drive the real boundary; inject, don't patch

Drive the real entry point and interacting components at the lowest level
that preserves the behavior under test. Use realistic peers or fakes at
external boundaries; do not mock away the interaction whose correctness the
test claims to prove.

- Do not double repo-owned code whose behavior the test claims. Use the
  shipped fakes (`band.testing.FakeAgentTools`) or, when a real dependency
  is too heavy, an in-memory fake with working behavior (stores what it is
  given, answers from that state) in the package `kit.py`, not a stub that
  returns canned values.
- `monkeypatch.setattr` is patching and follows the rule below;
  `monkeypatch.setenv`/`delenv` for configuration is fine. When repo-owned
  nondeterminism has no seam, pin it once in a documented fixture, as
  `tests/runtime/conftest.py` pins the resync jitter.
- A constructor seam (`create_process=`) or a public instance timeout of `0`
  ("check now / no grace") is the test hook. `unittest.mock.patch` of
  `asyncio.create_subprocess_exec`, a private `_close_process`, or a
  `_CALLBACK_EXIT_TIMEOUT_S` constant is not.
- `AsyncMock` is a double at an external boundary (a client the function
  *takes*), not a stand-in for the unit under test. A receipt-table test may
  mock the RPC client; it then does not count as coverage of the client's
  prompt path.
- Do not use autouse fixtures to replace product clients or bypass startup
  validation. Confine deliberate failure doubles to the tests that need them.
- Double HTTP at the transport with `pytest-httpx` (`httpx_mock`) or
  `httpx.MockTransport`. Its defaults fail the test on an unexpected request
  and on a registered response that was never requested; keep them.
- Use the baseline toolkit for needed live E2E coverage, not as a mandatory
  layer for every test.

## Test helpers and peers

- Put fixtures in `conftest.py`. Use fixtures or context managers for resource
  acquisition and confirmed cleanup, including failed or cancelled startup.
  Do not import fixtures through helper modules or collected test files.
- Keep scenario actions and outcome assertions visible in tests. Helpers may
  share mechanics and named assertions; they must not choose a scenario's
  expected outcome or hide its sequence.
- Do not create a runtime subclass for each payload, event, callback, or
  expected response. Drive simple inputs and callbacks explicitly in the test.
  Retain stateful doubles only for external behavior the test needs.
- Test mapping and validation at their owning function with real inputs. Test
  protocol, task context, ordering, and lifecycle with the real interacting
  components and a focused external peer where needed.
- Never reproduce the subject's reader, scheduler, callback-context propagation,
  retry, or cleanup implementation in a fake. Do not replace a running subject's
  methods to coordinate a test; use explicit gates or an observation seam.
- Keep each helper module focused on one concern. Share only necessary framing,
  correlation, and resource mechanics. Do not build an omnibus scenario
  simulator or move one unchanged into several files.
- Use one enum or constant definition for closed script, command, and status
  vocabularies. Reject unsupported commands and selections. No catch-all empty
  response, success, or silent no-op for an unsupported operation.
- Ground ordinary peer traces, including ordering, in current upstream source
  or contracts. Name inconsistent, missing, and stale frames as fault injection;
  they establish client behavior, not provider behavior.
- Assert observable decisions, delivery, state, and cleanup. Do not reassert
  fake setup flags or claim that a recorded call proves the resulting behavior.

**Review gate:** inspect changed helpers and peers with their consuming tests.
Remove duplicated setup and unnecessary simulation before extracting modules.
Reject any abstraction that hides the scenario, copies the subject, or requires
more machinery without removing repeated mechanics. Preserve meaningful
scenarios and verify that plausible regressions fail; passing counts and smaller
individual files do not establish a better test design.

## Isolation (non-live)

The CI unit job runs `pytest -n 4 --dist worksteal` with `pytest-randomly`
shuffling order and reseeding `random`. Every test must pass alone, in any
order, and beside any other test on another worker.

- Write files under `tmp_path`; never into the repo or a fixed `/tmp` path.
- Bind servers through `tests/ports.py` (`HeldPort`, `HeldAdjacentPorts`),
  not a fixed port.
- No module-level mutable state shared between tests; build it in a fixture.
- No live network. Doubles at the transport (above) or local peers only.
- Await or cancel every task, process, and server the test starts, including
  on failure, before the test ends (`AGENTS.md` "Test Synchronization").

## Time is a hung-test guard, not proof

Prefer time-agnostic tests. Prove ordering, concurrency, cancellation, and
lifecycle behavior with events, gates, barriers, observable state transitions,
and awaited completion (`AGENTS.md` "Test Synchronization").

The repo's controlled clock is `looptime` (`@pytest.mark.looptime`, or a
module `pytestmark`), which runs the event loop on virtual time. Run a test
under it when the code under test owns timers (resync intervals, retries,
heartbeats), so those timers and the suite's waits (`wait_for_condition`,
`Waiting.until`) never cost wall time. That is the environment, not the
proof.

**Deliberately advancing the clock belongs only in tests whose claim is
specifically “this deadline expires.”** Do not advance time merely to make
work run, establish ordering, or prove cleanup. In a deadline-expiry test, establish the
relevant wait before deliberately advancing the clock. Never use
`time.sleep`, elapsed wall time, `asyncio.sleep` of a grace period, or "it
finished within a duration" as proof. A fake that must hang until cancelled
awaits an `asyncio.Event` that is never set, not a long sleep.

- `async with asyncio.timeout(N)` around a test is a hung-test guard. It
  must not be the mechanism that establishes the outcome.
- To prove a timeout fail-closed path, use a public timeout of `0` when its
  contract supports "check now". When the claim requires deadline expiry,
  use `looptime`. Do not change validation to permit `0` or wait out the
  production grace period.
- `timeout = 0` proves "still pending at the wait". It does not prove the
  complementary branch: work that finishes before the wait succeeds. Cover
  that with a sequence that completes, not with a longer sleep.

Real subprocess and network readiness stay independent of a controlled
clock. Cleanup must account for resources created during failed or cancelled
startup and await their confirmed release.

Before advancing virtual time, await unrelated native responses already in
flight. A peer-side receipt does not prove all client response waits finished.

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
