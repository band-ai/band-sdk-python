---
name: band-python-conventions
description: Band Python SDK conventions for Python source, comments, docstrings, and error handling. Read before editing Python in this repository. Test files also follow band-python-test-conventions; this skill still owns SSoT, intent-named helpers, and public test seams.
paths:
  - "**/*.py"
---

# Coding Standards

- Always use type hints for function parameters and return types
- Use `from __future__ import annotations` as the first import in every file
- **Imports go at the top of the file, full absolute path (`from band.x.y
  import Z`), never inside a function body.** This gets missed constantly —
  check it explicitly before finishing any edit that touches an import. The
  one legitimate exception: a module gated behind an optional extra not
  installed in every lane's venv (e.g. `band.adapters.copilot_acp` imports
  `acp`, the `agent-client-protocol` package — importing it at module level
  would break test *collection* for a venv that lacks that extra, such as
  `dev-crewai`). Even then the deferred import belongs only at the specific
  call site that needs it, and only because collection-time safety genuinely
  requires it — never as a default habit. If the module has no such
  extra-gated dependency (true for the vast majority, including every
  adapter that only shells out to a CLI, like `codex`), the import is
  top-level, full stop.
- Import symbols directly from the module that owns them. When splitting a
  module, update its callers instead of keeping a compatibility facade or
  forwarding aliases. Do not preserve obsolete import paths. Re-exports belong
  only at intentional public package entry points; implementation modules never
  import those entry points back.
  Use `__all__` or explicit re-export aliases only to declare such a public API,
  not as boilerplate in ordinary modules.
- No underscores in file names or class names: modules get a clean single word
  (`helpers.py`, not `_utils.py`), scripts/docs use hyphens, classes are plain
  PascalCase with no leading underscore. Exception: patterns a tool requires,
  e.g. pytest's `test_*.py` / `conftest.py`.
- Never read configuration with `os.environ` / `os.getenv` — define a
  `pydantic-settings` `BaseSettings` class (field name == env var name,
  `SettingsConfigDict(extra="ignore", case_sensitive=False, env_ignore_empty=True)`
  — the last so a set-but-empty var like `CI=` falls back to the field default
  instead of raising a bool/int ValidationError) and read its fields; see
  `tests/e2e/baseline/settings.py` for the canonical pattern
- Prefer `match`/`case` over long `if`/`elif` chains that dispatch on one value
- Never use `print()` — use `logging` with module-level `logger = logging.getLogger(__name__)`
- Use `%s` placeholders in log messages for lazy evaluation
- Use Pydantic v2 for data models; use `model_dump()` not `dict()`
- Target Python 3.11+; use `list[str]` not `List[str]`, `str | None` not `Optional[str]`
- Use async/await everywhere in async codebases. `AsyncMock` is a test double
  at an external boundary, not a stand-in for the unit whose interaction a
  test claims to prove. Public constructor seams (a `create_process` factory,
  an instance timeout of `0` meaning "check now") beat `unittest.mock.patch`
  of private names or stdlib spawn. Tests follow
  `band-python-test-conventions`.
- Catch `pydantic.ValidationError` separately from generic `Exception`
- Use `raise ValueError(...)` for missing required config, not `logger.error()` + `sys.exit()`
- Never put issue-tracker references in code — no Linear issue IDs (e.g. `INT-123`), Linear URLs, or ticket numbers in comments, docstrings, or strings. Explain the *why* in plain terms instead. (Branch names, commit messages, and PR descriptions may reference issues.)
- Write intent-oriented code: the reader should see *what* is meant, not decode
  *how* it's done. Name for intent, keep flow obvious (guard clauses, `match`,
  early returns over nested branches), and hide bookkeeping behind a small helper
  or property with an intent-revealing name. Branch on *what to do*, not *which
  function to call*, and prefer computing the varying part once over
  duplicating a call across both branches of an `if`/`else` — e.g. a log
  statement that only varies by level: `level = logging.DEBUG if known else
  logging.WARNING; logger.log(level, msg, ...)`, never `log = logger.debug if
  known else logger.warning; log(msg, ...)` (a ternary-selected callable) and
  never `if known: logger.debug(msg, ...) else: logger.warning(msg, ...)`
  (the message and args retyped in both branches).
- Prefer a single source of truth for a value or closed vocabulary consumed in more
  than one place: give it one definition — a constant, a `StrEnum`, or a small helper
  — that every site references, rather than re-typing the same magic literal in a
  producer and the consumer that reads it (a typo then fails silently). This includes
  tests: a wire status, prompt text, or error message typed in a peer and again in an
  assertion is two copies. Keep genuinely distinct vocabularies separate, though —
  don't merge two sets that only happen to share some values today (e.g. the ACP
  `ChunkType` a chunk carries vs. the platform `message_type` an event is posted
  under).
- Before writing new logic, search the codebase for an existing helper that does
  or almost does it; extending it beats a parallel implementation.
- Use idiomatic constructs (`match`, decorators, context managers, recursion)
  only when they demonstrably reduce complexity.
- Split files that mix unrelated concerns; judge by cohesion, not line count.
- No `nonlocal` and no state smuggled out through closures. If a design forces
  it, restructure ownership instead; flat methods with real parameters beat
  nested closures.
- Prefer comprehensions over loops built around `continue`.
- Log failure paths and non-obvious branches; keep logs quiet and free of PII
  and secrets.
- Fix the root cause, never bolt on a patch: no special case, flag, wrapper,
  or `try`/`except` added at the call site. Prefer the fix that deletes or
  simplifies. If the same area needs a second or third fix, stop and rethink
  the design.


## Comment Style

Comments state facts about the code as it is now, not narration of how it
got there — never "extracted from X", "ported from Y", "changed from Z",
no session/PR/ticket/line-number history. Git already owns that history.

A comment earns its place only by saying something a reader can't get
from the code itself: a non-obvious invariant, a race/ordering guarantee,
a workaround for a specific external bug, a scope boundary that looks
like it should be wider than it is. Never restate what the code already
says in prose.

If a function needs a long comment to be understood, that's a signal the
function itself is doing too much — split it into named sub-functions
whose names carry the "what," and keep the comment for the one "why" that
can't be named away. Prefer trimming/removing this class of comment
outright over compressing it.

Docstrings follow PEP 257: a one-line summary first, in the imperative mood.


## Error Handling

Beyond the `ValidationError`/`ValueError` rules above, format tool validation errors with `format_tool_validation_error`
(`src/band/runtime/tools/schema.py`) or `format_validation_error`
(`src/band/runtime/custom_tools.py`) rather than hand-rolling the message. Who
acts on each exception class is in `src/band/core/exceptions.py`.
