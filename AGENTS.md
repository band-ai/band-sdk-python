# Band Python SDK

This is a Python SDK that connects AI agents to the Band collaborative platform.

## Core Features

1. Multi-framework support (LangGraph, Anthropic, CrewAI, Claude SDK, Copilot SDK, Codex, Pydantic AI, Parlant, Gemini, Letta, Google ADK, OpenCode, Agno, Strands Agents)
2. A2A protocol support: Bridge to remote A2A agents and expose Band peers as A2A endpoints
3. ACP integration: Editor-facing server and client adapters over stdio or TCP (Cursor, Codex, Claude Code, GitHub Copilot)
4. Platform tools for chat, contacts, memory, and file management
5. WebSocket + REST transport: Real-time messaging with REST API fallback

## Platform Tools

`src/band/runtime/tools.py` owns every word an LLM reads about a platform
tool (chat, contacts, memory, files) — reach for `platform_args_schema`/
`@platform_tool`/the schema helpers instead of retyping tool description
text. See [docs/platform-tools.md](docs/platform-tools.md) for the full tool
inventory, the modeling pattern, and the drift guardrail test.

## Adapter Feature Flags & Capability Negotiation

Every adapter constructor takes `emit=`/`capabilities=`/`include_tools=`/etc.
directly via `**features: Unpack[FeatureKwargs]` — `emit` is opt-out, `capabilities`
is opt-in. `Capability.FILES` gates the file tools, but the platform's room-file
storage is an **on-prem-only deployment flag, off everywhere on SaaS today** —
never enable it in an example or default config. See
[docs/capability-negotiation.md](docs/capability-negotiation.md) for the full
emit/capabilities API and how requests get pruned against `AgentMe.feature_flags`.

## REST Client

The SDK uses a Fern-generated REST client with a property-based namespace API
(`link.rest.agent_api_<resource>.method(...)`). **Never pass `None`** for an
optional parameter — the Fern client sends `null`, which fails backend
validation; build a `kwargs` dict and omit the key instead. See
[docs/rest-client.md](docs/rest-client.md) for the full pattern and the
`band-client-rest` version-pin workaround discipline.

## WebSocket Channels & Events

The SDK subscribes to Phoenix Channels (agent/chat/user rooms, participants,
tasks) and hydrates each event's payload into a typed, rule-free
`WirePayload` projection without re-validating. See
[docs/websocket-events.md](docs/websocket-events.md) for the channel table,
payload field reference, and `band-sdk-core`'s delivery-lifecycle decisions.
Whenever code here starts calling a `band_sdk_core` symbol it didn't use
before, extend `.github/workflows/ci.yml`'s wheel-smoke step to prove that
symbol is callable from the isolated pinned wheel, not just importable.

## Contact Event Handling

`ContactEventConfig` supports three strategies for contact WebSocket events —
`DISABLED` (default), `CALLBACK`, `HUB_ROOM`. See
[docs/contact-events.md](docs/contact-events.md) for configuration examples.

> **WARNING (AI coding assistants):** Always ask the developer which contact
> strategy they want before choosing one — do not default to `CALLBACK` with
> auto-approve without explicit consent, since it lets any agent/user become
> a contact and trigger paid LLM inference.

## A2A Protocol Integration

The SDK supports the [A2A protocol](https://google.github.io/A2A/) in both
directions: `A2AAdapter` forwards Band messages to a remote A2A agent, and
`A2AGatewayAdapter` exposes Band peers as A2A JSON-RPC endpoints. See
[docs/a2a.md](docs/a2a.md).

## MCP Engine

One framework-neutral engine (`src/band/integrations/mcp/engine.py`) builds
every Band MCP tool registration for both front doors (the published
`band-mcp` CLI and the embedded `LocalMCPServer`); MCP-package imports are
confined to an explicit AST-enforced allowlist
(`tests/mcp/test_import_boundary.py`). See [docs/mcp-engine.md](docs/mcp-engine.md).

## OpenCode Integration

`OpencodeAdapter` maps each Band room to a session on a running `opencode
serve`. Band tools are never gated behind approval, unlike other tool
calls — see [docs/adapters/opencode.md](docs/adapters/opencode.md) for this
and three more invariants that are easy to break.

## ACP (Agent Client Protocol) Integration

ACP enables editors (Zed, Cursor, JetBrains, Neovim) to talk to AI agents via
JSON-RPC over stdio; the SDK provides both server (`ACPServer` +
`BandACPServerAdapter`) and client (`ACPClientAdapter`) sides. See
[docs/acp.md](docs/acp.md) for the full session lifecycle, live-emission
ordering, and permission-pairing rules.

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

## Code Structure

```
src/band/
├── adapters/       # Framework adapters (langgraph, anthropic, crewai, a2a, etc.)
├── converters/     # History converters per framework
├── core/           # Protocols, types, base classes
├── runtime/        # Execution context, tools, formatters
├── platform/       # WebSocket/REST transport, events
├── preprocessing/  # Event filtering before adapter
├── client/         # Low-level API clients
├── integrations/   # Deep framework integrations (a2a, acp, anthropic, claude_sdk, langgraph, parlant, pydantic_ai)
├── config/         # Configuration management, YAML loading, env parsing
├── testing/        # Testing utilities (fake tools, test helpers)
└── agent.py        # Main entry point
```

## Testing Structure

```
tests/
├── adapters/       # Unit tests per adapter (mocked)
├── converters/     # Unit tests per converter
├── core/           # Core logic tests
├── runtime/        # Runtime tests
├── integration/    # Real API tests (skipped in CI)
├── e2e/            # End-to-end tests (requires live platform + LLM keys)
│   └── baseline/   # The only E2E suite: reusable toolkit + smokes (see baseline/README.md)
├── skills/         # Tests for .claude/skills scripts (paths via tests/paths.py anchors)
└── conftest.py     # Shared fixtures
```

`testpaths = ["tests"]`, so **every** test lives here — including tests for code
outside `src/band` (`band-bridge` -> `tests/bridge`, `docker/band_python_kit` ->
`tests/docker`, `.claude/skills` -> `tests/skills`). A `test_*.py` placed next to
non-package code is never collected by CI's bare `uv run pytest`; address the code
under test through an anchor in `tests/paths.py` instead.

Before writing a new E2E test or helper, read `tests/e2e/baseline/README.md`
— it documents the reusable baseline toolkit (provisioning, user ops, reply
capture, judge, assertions, fixtures) so you reuse it instead of rebuilding it.
To wire a new framework adapter into the matrix, follow
`tests/e2e/baseline/ADDING_AN_ADAPTER.md`.

## Commands

```bash
# Install dependencies (all extras except crewai and parlant — see Dependency Conflicts below)
uv sync --extra dev

# Install crewai adapter deps (isolated from dev/parlant/pydantic-ai)
uv sync --extra dev-crewai

# Install parlant adapter deps (isolated from dev/crewai/pydantic-ai)
uv sync --extra dev-parlant

# Run unit tests
uv run pytest tests/ --ignore=tests/integration/ --ignore=tests/e2e/ -v

# Run single test
uv run pytest tests/ -k "test_name"

# Run with coverage
uv run pytest tests/ --ignore=tests/integration/ --ignore=tests/e2e/ --cov=src/band

# Run integration tests (requires API key)
uv run pytest tests/integration/ -v -s --no-cov

# Run E2E tests (requires live platform + LLM API keys)
E2E_TESTS_ENABLED=true uv run pytest tests/e2e/ -v -s --no-cov

# Run E2E tests for a single adapter
E2E_TESTS_ENABLED=true uv run pytest tests/e2e/ -k langgraph -v -s --no-cov

# Run the baseline toolkit smokes (provision their own agents; only need
# BAND_API_KEY_USER — see tests/e2e/baseline/README.md)
E2E_TESTS_ENABLED=true uv run pytest tests/e2e/baseline/ -v -s --no-cov

# Linting and formatting
uv run ruff check .
uv run ruff format .
uv run pyrefly check
```

## Dependency Conflicts

**crewai cannot coexist** with parlant or pydantic-ai in the same Python
environment (conflicting pydantic/opentelemetry-sdk version ceilings), and
**parlant cannot coexist with pydantic-ai** either (a `griffe`/`griffelib`
namespace collision). That's why there are three separate extras — `dev`
(everything except crewai and parlant), `dev-crewai`, `dev-parlant` — each
installed in its own CI job/venv. See
[docs/dependency-conflicts.md](docs/dependency-conflicts.md) for the full
version table and the mechanism behind each conflict.

## Environment Variables

When running examples, live probes, integration checks, or provisioning
against a real Band platform, load these from the repo-root `.env.test` —
not ad-hoc `.env` copies, shell leftovers, or invented values — and never
print secret values from it. Example-local `.env` files (e.g.
`examples/**/.env`) may still hold Docker/`GITHUB_TOKEN` config, but Band
agent keys and platform URLs should stay aligned with `.env.test` /
`agent_config.yaml` rather than a second source of truth.

- `BAND_REST_URL`: REST API URL (default: https://app.band.ai)
- `BAND_WS_URL`: WebSocket URL (default: wss://app.band.ai/api/v1/socket/websocket)
- `BAND_API_KEY_USER`: User API key for E2E WebSocket observer and trigger messages (the only Band key the baseline toolkit needs — it provisions its own agents)
- `BAND_API_KEY_USER_2`: Optional second user key, for baseline smokes exercising two-user interaction
- `OPENAI_API_KEY`: OpenAI API key (for LangGraph examples)
- `ANTHROPIC_API_KEY`: Anthropic API key (for Anthropic/Claude SDK examples)
- `GOOGLE_API_KEY`: Google API key for Gemini Developer API (for Gemini/Google ADK examples)
- `GOOGLE_GENAI_USE_VERTEXAI`: Set to `true` to use Vertex AI instead of Gemini Developer API
- `GOOGLE_CLOUD_PROJECT`: Google Cloud project ID (required when using Vertex AI)
- `GITHUB_TOKEN`: A Copilot-entitled GitHub token. The baseline `copilot_sdk` and `copilot_acp` builders use Anthropic BYOK and never read it; the only baseline reader is the single Copilot-hosted auth smoke (`test_copilot_hosted_auth_replies`, skips when unset). Also used by Copilot-hosted examples outside the baseline; optional when a stored `copilot login` is present.
- `E2E_TESTS_ENABLED`: Set to `true` to enable E2E tests (default: disabled)
- `E2E_LLM_MODEL`: OpenAI model for E2E tests (default: `gpt-5.4-mini`)
- `E2E_ANTHROPIC_MODEL`: Anthropic model for E2E tests (default: `claude-haiku-4-5` — the baseline judge uses structured outputs, which older Haiku models do not support)
- `E2E_JUDGE_MODEL`: Anthropic model for the baseline LLM judge (default: falls back to `E2E_ANTHROPIC_MODEL`; must support structured outputs)
- `E2E_TIMEOUT`: Per-turn response timeout in seconds for E2E tests (default: `120`; a slow test can add headroom with `@pytest.mark.timeout(extra=n)`)
- `DOCKER_TESTS_ENABLED`: Set to `true` to run `docker_build`-marked tests (e.g. `tests/docker/test_band_python_kit.py`), which shell out to a real `docker build`/`docker run` (default: disabled everywhere, including CI — CI runners do have a Docker daemon, unlike the nested-virtualization `sbx` tests, so this needs the same explicit opt-in as `E2E_TESTS_ENABLED` rather than a plain Docker-availability check)

Baseline lane scoping (see `tests/e2e/baseline/README.md`):

- `BAND_E2E_LANE`: The CI lane (a job: a `uv` extra + optional server/CLI setup) to scope the run to. Lane ids are content-based and decoupled from the `uv` extra — `core` (anthropic/openai-family adapters plus `copilot_sdk`, which self-downloads its CLI runtime and uses Anthropic BYOK without GitHub auth; `dev` extra), `crewai` (`dev-crewai` extra), `google` (gemini/google_adk, split out for rate-limit isolation), `backends` (codex + opencode coding agents), `letta` (self-hosted letta server), `parlant` (`dev-parlant` extra — split from `core` because parlant's griffe/griffelib transitive deps collide with pydantic_ai's; registers no matrix adapter, a bespoke `@lane`-pinned smoke only). Resolves the lane's adapters from the registry (`ci_lanes()`, derived from each adapter's `requires`); out-of-lane adapters skip-with-reason (they're covered by their own lane) while in-lane adapters keep fail-loud (an unwired backend stays red). Unset (the local default) = full matrix, no scoping. CI never lists adapters — it derives lanes from the registry. A test's lane is derived from **all** the frameworks it touches (a matrix cell's adapter plus its `@per_adapter(peer=...)`, or a `@with_adapters` set); a test whose frameworks span more than one home lane fails collection (`assert_every_item_is_schedulable`) unless pinned with `@lane(Lane.X)` to a lane whose extra hosts them all. To add a lane, see `tests/e2e/baseline/README.md` ("Adding a CI lane").

Baseline provisioning/cleanup policy (see `tests/e2e/baseline/README.md`):

- `BAND_E2E_AUTOCLEAN`: Reap provisioned agents + rooms on teardown (default: `true`; set `false` to keep resources for debugging a failing run)
- `BAND_E2E_ORPHAN_SWEEP`: Sweep leftover agents from crashed prior runs at session start (default: `true`)
- `BAND_E2E_ORPHAN_MAX_AGE_MINUTES`: Only sweep agents older than this, so a concurrent run is never reaped mid-flight (default: `120`)
- `BAND_E2E_SCORECARD_JSON`: Write this run's adapter×test scorecard (pass/fail/skip + N/A reasons) as JSON to this path at session end (default: empty = don't emit). CI sets one path per lane; a final job merges them (see `tests/e2e/baseline/scorecard.py` and the Scorecard section of the baseline README)

## Adding a New Framework Integration

Follow the 7-phase TDD workflow (scaffold source files, register with
conformance infrastructure, implement the converter, implement the adapter,
write framework-specific tests, final validation) documented in
[docs/adding-a-framework-integration.md](docs/adding-a-framework-integration.md)
when adding a new adapter and converter — it also has the exact conformance
test commands to run at each phase and a key-files reference table.

## Example Files (examples/ directory)

Every file under `examples/` needs PEP 723 inline script metadata at the top
(so `uv run examples/<framework>/<file>.py` works standalone), plus a handful
of other conventions (credentials via `load_agent_config`, `async with agent:
await agent.run_forever()`, etc.) — see
[docs/examples-guide.md](docs/examples-guide.md).

## Documentation Testing (markdown snippets)

Tracked `.md` files (except `examples/`) run in CI as tests via `pytest-markdown-docs`
— so `python` snippets in the docs must stay correct and runnable, not rot:

```bash
uv run pytest --markdown-docs $(git ls-files '*.md' ':!:examples/*') --no-cov
```

Fence tags: plain ` ```python ` **executes** (top-level `assert`s are the
checks); ` ```python notest ` is collected out — reserve it for pseudo-code,
placeholder names, or snippets that genuinely need a live platform/LLM;
` ```python fixture:<name> ` executes with that pytest fixture (resolved from
the nearest `conftest.py`) injected into the block's namespace. Prefer a
runnable block with a small `assert` over reaching for `notest`, so a rename
breaks the doc.

**Gotcha — snippets under `tests/e2e/**` skip in CI**, silently, since that
tree's conftest skips every collected item unless `E2E_TESTS_ENABLED=true`
and CI's markdown-docs step never sets it — worse than honest `notest`. Keep
E2E-doc snippets `notest`; put a runnable check of E2E-adjacent symbols in a
doc outside `tests/e2e/**` instead, where it actually executes.

## External Research & Code Reuse

Search for an existing, well-maintained library before hand-rolling a
nontrivial mechanism (cache, retry/backoff, rate limiting). Before
committing to one, verify the capability you need ships in the *released*
version you'd actually install, not just on `latest`/`master` docs — `pip
install` it and exercise the real call in this repo's venv. See
[docs/external-research.md](docs/external-research.md) for the
provenance/trust/maintainability checklist and a live example of a library
whose docs described an unreleased feature.

## Coding Standards

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
- In tests, never derive repository-anchored paths with per-file
  `Path(__file__).parents[N]` arithmetic — import the anchors from
  `tests/paths.py` (`REPO_ROOT`, `SRC_ROOT`, `EXAMPLES_ROOT`, `KIT_DIR`,
  `ENV_TEST_FILE`). Only genuinely package-relative paths (a fixture file
  next to its test) stay relative to their own `__file__`.
- Prefer `match`/`case` over long `if`/`elif` chains that dispatch on one value
- Never use `print()` — use `logging` with module-level `logger = logging.getLogger(__name__)`
- Use `%s` placeholders in log messages for lazy evaluation
- Use Pydantic v2 for data models; use `model_dump()` not `dict()`
- Target Python 3.11+; use `list[str]` not `List[str]`, `str | None` not `Optional[str]`
- Use async/await everywhere in async codebases; use `AsyncMock` for testing async methods
- Catch `pydantic.ValidationError` separately from generic `Exception`
- Use `raise ValueError(...)` for missing required config, not `logger.error()` + `sys.exit()`
- Never put issue-tracker references in code — no Linear issue IDs (e.g. `INT-123`), Linear URLs, or ticket numbers in comments, docstrings, or strings. Explain the *why* in plain terms instead. (Branch names, commit messages, and PR descriptions may reference issues.)
- Test what really matters — behavior that can break. Don't write tests that
  restate definitions (asserting dataclass defaults equal themselves, echoing a
  constant) or otherwise cannot fail for a real reason; they add maintenance
  cost without protection.
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
- Prefer a single source of truth for a value or closed vocabulary consumed in more
  than one place: give it one definition — a constant, a `StrEnum`, or a small helper
  — that every site references, rather than re-typing the same magic literal in a
  producer and the consumer that reads it (a typo then fails silently). Keep genuinely
  distinct vocabularies separate, though — don't merge two sets that only happen to
  share some values today (e.g. the ACP `ChunkType` a chunk carries vs. the platform
  `message_type` an event is posted under).

## Pre-Commit Checklist

Before running the commands below, re-read your own diff once against the
Coding Standards above — including code you just wrote this session, not only
code you started from. The two rules that get skipped under time pressure:

- **Single source of truth**: a literal, magic string, or multi-line block
  re-typed in more than one place (a second copy you just wrote counts) instead
  of one `StrEnum` / constant / small helper every site references.
- **Intent-oriented code**: a raw comprehension or dict-poke standing in for a
  small, intent-named helper — e.g. `[e for e in tools.events_sent if
  e["message_type"] == "x"]` repeated at each call site instead of one
  `events_of_type(tools, "x")`.

Ruff/pyrefly/pytest catch correctness and style; they do not catch either of
these, so this step is the only gate for them.

```bash
uv run ruff check .
uv run ruff format .
uv run pyrefly check
uv run pytest tests/ --ignore=tests/integration/ --ignore=tests/e2e/ -v
```

## Error Handling

Beyond the `ValidationError`/`ValueError` rules already in Coding Standards
above, see [docs/error-handling.md](docs/error-handling.md) for
validation-error formatting, exception-hierarchy, and error-message guidance.

## Git Workflow

### Branch Naming

Branch names should match the Linear issue:

- Format: `<prefix>/<title>-<ISSUE-ID>`
- Example: `feat/add-user-auth-ENG-123`

Prefixes:

- `feat/` - New features
- `fix/` - Bug fixes
- `refactor/` - Code refactoring
- `docs/` - Documentation changes
- `chore/` - Maintenance tasks

#### Creating Branches from Linear Issues

Use `git lb` to create properly named branches from Linear issues:

```bash
git lb INT-84
```

This automatically fetches the issue title from Linear and creates a branch with the correct naming convention.

If `git lb` is not installed, ask the developer for the proper branch name.

### Commit Messages

Follow conventional commits format for all commits:

```
<type>: <description>

[optional body]

[optional footer]
```

Types:
- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation only
- `refactor:` - Code refactoring
- `test:` - Adding or updating tests
- `chore:` - Maintenance tasks

### Pull Request Titles

PR titles MUST use the same conventional commits format as commit messages
(above), e.g.:

- `feat: Add custom tools support to all adapters`
- `fix: Handle validation errors in execute_tool_call`
- `docs: Update README with new adapter examples`

### Pre-Commit Checklist

See [Pre-Commit Checklist](#pre-commit-checklist) above — one checklist, not two.

### Code Review

- Keep PRs focused and reasonably sized
- Respond to review comments promptly
- Squash commits when merging if history is messy

## GitHub PR Inline Comments

Post inline review comments via the GitHub Reviews API
(`gh api repos/{owner}/{repo}/pulls/{pr_number}/reviews --method POST --input -`,
JSON piped through a heredoc) — never `gh pr review --comment` (that adds a
general comment, not inline ones) and never diff line numbers (use line
numbers from the file's new version, e.g. via `gh pr view {pr} --json
headRefOid -q .headRefOid` then fetching that commit's file content). See
[docs/github-pr-inline-comments.md](docs/github-pr-inline-comments.md) for
the full workflow and a worked example.
