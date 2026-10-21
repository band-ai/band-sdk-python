---
name: band-python-test-conventions
description: Band Python SDK test conventions. Use when writing or editing tests under tests/.
paths:
  - "tests/**/*.py"
---

# Test Conventions

- In tests, never derive repository-anchored paths with per-file
  `Path(__file__).parents[N]` arithmetic — import the anchors from
  `tests/paths.py` (`REPO_ROOT`, `SRC_ROOT`, `EXAMPLES_ROOT`, `KIT_DIR`,
  `ENV_TEST_FILE`). Only genuinely package-relative paths (a fixture file
  next to its test) stay relative to their own `__file__`.
- Test what really matters — behavior that can break. Don't write tests that
  restate definitions (asserting dataclass defaults equal themselves, echoing a
  constant) or otherwise cannot fail for a real reason; they add maintenance
  cost without protection.
- **Tests must be declarative and intent-revealing, not a transcript of the
  implementation.** Assert on a readable projection of the observable outcome
  — the thing the test is actually about — never on raw internals or on a
  side effect that merely implies the real answer. Concretely:
  - `assert reply.outline == ["tool_call (permission)", "message", ...]` over
    a hand-rolled comprehension pulling `message_type` out of each event dict.
  - `assert record.levelno == logging.DEBUG` over inferring a log level
    indirectly from whether two separate capture windows came back empty.
  If writing the assertion requires re-deriving *how* the code decided
  something, the test is checking the wrong thing — assert the decision
  itself.
- **Tests run on Ubuntu and Windows CI — test across OSes, don't skip one.**
  Build host paths with `tmp_path` or `tests.paths.host_absolute_path`, never a
  hard-coded `"/opt/..."` (no drive, so relative on Windows); check absoluteness
  with `band.workspaces.is_host_absolute`. Where behavior differs per OS, write
  one parametrize table that runs everywhere, each row naming the `os.name`
  values that accept it. `skipif(os.name ...)` is only for a boundary that
  cannot run on that OS, never for a path or separator assumption.
- Decide what correct looks like before running the code, then assert that;
  never bend an assertion to match whatever the code currently produces.
- A skipped, disabled, or silently ignored failing test is always a blocking
  issue.
- A fix for a confirmed bug comes with a test that fails before the fix and
  passes after it.
- For a stateful lifecycle component, add a Hypothesis model-based test over
  generated step sequences (see `tests/platform/linkclose/test_lifecycle_model.py`).
- Ordering, concurrency and timeout behavior is proven with explicit
  synchronization and a controlled clock: `AGENTS.md` "Test Synchronization".
- File layout (one test class per file, packages for larger suites):
  `AGENTS.md` "Testing Structure".
