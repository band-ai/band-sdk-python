# Band Python SDK

This is a Python SDK that connects AI agents to the Band collaborative platform.

## Core Features

1. Multi-framework support (LangGraph, Anthropic, CrewAI, Claude SDK, Copilot SDK, Codex, Pydantic AI, Parlant, Gemini, Letta, Google ADK, OpenCode, Agno, Strands Agents, NOOA)
2. A2A protocol support: Bridge to remote A2A agents and expose Band peers as A2A endpoints
3. ACP integration: Editor-facing server and client adapters over stdio or TCP (Cursor, Codex, Claude Code, GitHub Copilot)
4. Platform tools for chat, contacts, memory, files, and the room task board
5. WebSocket + REST transport: Real-time messaging with REST API fallback

## Skills

Project guidelines live in skills under `.claude/skills/`; Codex reads the
same skills through the `.agents/skills` symlink. Each loads automatically
when its trigger matches; read the SKILL.md directly if your agent doesn't
load skills.

| Skill | Use when |
|---|---|
| [band-python-conventions](.claude/skills/band-python-conventions/SKILL.md) | writing or editing Python source |
| [band-python-test-conventions](.claude/skills/band-python-test-conventions/SKILL.md) | writing or editing tests under `tests/` |
| [band-python-comment-style](.claude/skills/band-python-comment-style/SKILL.md) | writing comments or docstrings |
| [band-python-error-handling](.claude/skills/band-python-error-handling/SKILL.md) | raising/handling exceptions, formatting validation errors |

| Skill | Use when |
|---|---|
| [band-plan](.claude/skills/band-plan/SKILL.md) | turning a Linear ticket into a reviewed implementation plan |
| [band-linear-create-branch](.claude/skills/band-linear-create-branch/SKILL.md) | creating a branch for Linear work with `git lb` |
| [band-add-framework-adapter](.claude/skills/band-add-framework-adapter/SKILL.md) | adding a new framework adapter |
| [band-write-e2e-test](.claude/skills/band-write-e2e-test/SKILL.md) | writing or extending an E2E baseline test |
| [band-run-e2e-live](.claude/skills/band-run-e2e-live/SKILL.md) | running the E2E baseline lanes against the live platform |
| [band-investigate-test-error](.claude/skills/band-investigate-test-error/SKILL.md) | finding the root cause of a failing or flaky test |
| [band-code-review](.claude/skills/band-code-review/SKILL.md) | running the team's fixed-criteria review on a PR or branch |
| [band-here-is-a-review](.claude/skills/band-here-is-a-review/SKILL.md) | handling review feedback on a PR or pasted review findings |
| [band-pr-triage](.claude/skills/band-pr-triage/SKILL.md) | a quick read-only check of whether a PR can merge or needs a full review |
| [band-stacked-prs](.claude/skills/band-stacked-prs/SKILL.md) | splitting work into stacked PRs or managing a PR chain with `gh stack` |
| [band-desktop-setup](.claude/skills/band-desktop-setup/SKILL.md) | setting up, upgrading, or troubleshooting Claude Desktop as a Band agent |
| [bug-hunting-via-example](.claude/skills/bug-hunting-via-example/SKILL.md) | using real runnable examples to reproduce and root-cause integration, concurrency, or lifecycle bugs |
| [dependabot-triage](.claude/skills/dependabot-triage/SKILL.md) | reviewing, batching, or clearing open Dependabot PRs |
| [local-platform-testing](.claude/skills/local-platform-testing/SKILL.md) | testing against a locally run thenvoi-platform, including on-prem-only feature flags |

## Platform Tools

`src/band/runtime/tools/` owns every word an LLM reads about a platform
tool (chat, contacts, memory, files, tasks) — reach for `platform_args_schema`/
`@platform_tool`/the schema helpers instead of retyping tool description
text. `tests/framework_conformance/test_tool_text_drift.py` is the drift
guard; an adapter that builds its own tool schemas sets `advertised_arg_text`
on its test config so the guard can read what the model actually sees.

## Turn Outcome

`SimpleAdapter.run_judged_turn` (called by `on_event`) judges every turn with band-sdk-core's rule: a turn
that replied, declined via `band_no_reply`, did real work, was settled by the
adapter, or already reported a failure completes; anything else is reported
once and marked FAILED. Adapters never judge turns themselves. Post an
adapter's own messages with `send_notice` (never `send_message`, which counts as
the reply) and relay a model's final text only through `relay_reply`. See
[docs/turn-outcome.md](docs/turn-outcome.md).

## Adapter Constructor Shape

Every adapter is built as `XAdapter(config: XAdapterConfig | None = None, *,
history_converter=..., additional_tools=..., <live objects>, **features)`.
`config` is required (no `None` default) only when no usable default exists,
such as a remote endpoint or app credentials.
`XAdapterConfig` subclasses `BaseAdapterConfig` (or `EnvAdapterConfig` when env
vars may set it) from `src/band/core/adapterconfig.py`. It is frozen, rejects
unknown fields, and holds plain data only, so it loads from YAML/JSON. Clients,
graphs, LLM objects, factories and callbacks stay keyword-only constructor
arguments. Validate settings in the config, not the constructor.
`tests/framework_conformance/adaptershape/test_adaptershape.py` enforces the shape.

## Adapter Feature Flags & Capability Negotiation

Every adapter constructor takes `emit=`/`capabilities=`/`include_tools=`/etc.
directly via `**features: Unpack[FeatureKwargs]` — `emit` is opt-out, `capabilities`
is opt-in. `Capability.FILES` gates the file tools, but the platform's room-file
storage is an **on-prem-only deployment flag, off everywhere on SaaS today** —
never enable it in an example or default config. Requests are pruned against
`AgentMe.feature_flags` (`CAPABILITY_FEATURE_FLAGS` in
`src/band/runtime/capabilities.py`); never add a flag key the platform doesn't
send yet, since a missing key prunes that capability on every deployment.

## REST Client

The SDK uses a Fern-generated REST client with a property-based namespace API
(`link.rest.agent_api_<resource>.method(...)`). **Never pass `None`** for an
optional parameter — the Fern client sends `null`, which fails backend
validation; build a `kwargs` dict and omit the key instead
(`tests/platform/test_rest_client.py` pins that wire behavior). See
[docs/rest-client.md](docs/rest-client.md) for the `band-client-rest`
version-pin workaround discipline.

## WebSocket Channels & Events

The SDK subscribes to Phoenix Channels in
`src/band/client/streaming/client.py` and hydrates each event's payload into a
typed, rule-free `WirePayload` projection without re-validating. See
[docs/websocket-events.md](docs/websocket-events.md) for which rules and
delivery-lifecycle decisions live in `band-sdk-core` instead.
CI's isolated-wheel smoke (`.github/scripts/wheel-smoke.py`) finds every
`band_sdk_core` name the installed `band` package refers to and fails if the
pinned wheel lacks one, so a new core symbol needs no manual CI step.

## Contact Event Handling

`ContactEventConfig` supports three strategies for contact WebSocket events —
`DISABLED` (default), `CALLBACK`, `HUB_ROOM`; the `ContactEventConfig`
docstring in `src/band/runtime/contacts.py` has configuration examples.

> **WARNING (AI coding assistants):** Always ask the developer which contact
> strategy they want before choosing one — do not default to `CALLBACK` with
> auto-approve without explicit consent, since it lets any agent/user become
> a contact and trigger paid LLM inference.

## A2A Protocol Integration

The SDK supports the [A2A protocol](https://google.github.io/A2A/) in both
directions: `A2AAdapter` forwards Band messages to a remote A2A agent, and
`A2AGatewayAdapter` exposes Band peers as A2A JSON-RPC endpoints.

## MCP Engine

One framework-neutral engine (`src/band/integrations/mcp/engine.py`) builds
every Band MCP tool registration for both front doors (the published
`band-mcp` CLI and the embedded `LocalMCPServer`); MCP-package imports are
confined to an explicit AST-enforced allowlist
(`tests/mcp/test_import_boundary.py`).

## OpenCode Integration

`OpencodeAdapter` maps each Band room to a session on a running `opencode
serve`. Band tools are never gated behind approval, unlike other tool
calls — see [docs/adapters/opencode.md](docs/adapters/opencode.md) for this
and four more invariants that are easy to break.

## ACP (Agent Client Protocol) Integration

ACP enables editors (Zed, Cursor, JetBrains, Neovim) to talk to AI agents via
JSON-RPC over stdio; the SDK provides both server (`ACPServer` +
`BandACPServerAdapter`) and client (`ACPClientAdapter`) sides. See
[docs/acp.md](docs/acp.md) for the full session lifecycle, live-emission
ordering, and permission-pairing rules.

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

## Test Synchronization

Tests must prove ordering, concurrency, and lifecycle behavior through explicit
synchronization: events, gates, barriers, or observable state transitions. Never
use elapsed wall time, fixed sleeps, or an assumption that an operation finishes
within a duration to establish correctness. Hold an operation at the relevant
boundary, assert the behavior while it is blocked, then release it and await
confirmed completion.

Test timeout, expiry, and deadline behavior with a controlled clock. Keep real
subprocess and network readiness independent of that clock. Real-time timeouts
may guard against a hung test, but must not drive the scenario or serve as proof
of the behavior under test. Cleanup must account for resources created during
failed or cancelled startup and await their confirmed release.

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

**One test class per file, one concern per class.** Each `test_*.py` holds
exactly one `class Test...` named for the behavior it covers — no
module-level test functions and no helper classes beside it. When a suite
outgrows one file, make it a package: `conftest.py` for its fixtures, a plain
helper module (e.g. `kit.py`) for shared doubles and helpers, and one test file
per concern. Apply this to every test file you add, and split a file you
touch that breaks it.

Before writing a new E2E test or helper, read `tests/e2e/baseline/README.md`
— it documents the reusable baseline toolkit (provisioning, user ops, reply
capture, judge, assertions, fixtures) so you reuse it instead of rebuilding it.
To wire a new framework adapter into the matrix, follow
`tests/e2e/baseline/ADDING_AN_ADAPTER.md`.

## Commands

```bash
# Install dependencies (modern frameworks; see Dependency Conflicts below)
uv sync --extra dev

# Install crewai adapter deps (isolated from dev/parlant/pydantic-ai)
uv sync --extra dev-crewai

# Install Strands/NOOA adapter deps in their OpenAI-2 environment
uv sync --extra dev-strands-nooa

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
environment (crewai's exact `~=` pins on `mcp`, `aiofiles`, `regex` and `tomli` drag the
whole lock), and **parlant cannot coexist with pydantic-ai** either (a
`griffe`/`griffelib` namespace collision uv cannot see as a version conflict).
Strands and NOOA/LiteLLM require OpenAI 2, while modern Pydantic AI requires
OpenAI 3. There are four isolated development extras: `dev` (modern frameworks),
`dev-strands-nooa` (Strands, conditional NOOA, and their LangGraph peer), `dev-crewai`,
and `dev-parlant`. Each has its own CI job/venv. Public LangGraph and Parlant
remain compatible with either OpenAI major; LangGraph + Strands is supported.
Use `uv run --locked --isolated --python 3.12 --all-packages --extra <extra>`
for concurrent work so one lane never overwrites another lane's environment. The `[tool.uv] conflicts` comments in `pyproject.toml` carry the
mechanism.

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
- `GITHUB_TOKEN`: A Copilot-entitled GitHub token. The baseline `copilot_sdk` and `copilot_acp` builders use Anthropic BYOK and never read it; the only baseline readers are the Copilot-hosted smokes (`test_copilot_hosted_auth_replies`, `test_copilot_switches_model_and_effort_in_a_live_room`, and the `hosted-effort` cell of `test_copilot_turn_fails_loudly_on_an_unadvertised_selection`, which skip when unset). Also used by Copilot-hosted examples outside the baseline; optional when a stored `copilot login` is present.
- `E2E_TESTS_ENABLED`: Set to `true` to enable E2E tests (default: disabled)
- `E2E_LLM_MODEL`: OpenAI model for E2E tests (default: `OPENAI_MODEL` in `src/band/core/defaultmodels.py`, the single source for every default model id)
- `E2E_ANTHROPIC_MODEL`: Anthropic model for E2E tests (default: `ANTHROPIC_MODEL` — Sonnet handles ambiguous multi-agent turns that Haiku does not; must support structured outputs for the baseline judge)
- `E2E_JUDGE_MODEL`: Anthropic model for the baseline LLM judge (default: falls back to `E2E_ANTHROPIC_MODEL`; must support structured outputs)
- `E2E_TIMEOUT`: Per-turn response timeout in seconds for E2E tests (default: `120`; a slow test can add headroom with `@pytest.mark.timeout(extra=n)`)
- `DOCKER_TESTS_ENABLED`: Set to `true` to run `docker_build`-marked tests (e.g. `tests/docker/test_band_python_kit.py`), which shell out to a real `docker build`/`docker run` (default: disabled everywhere, including CI — CI runners do have a Docker daemon, unlike the nested-virtualization `sbx` tests, so this needs the same explicit opt-in as `E2E_TESTS_ENABLED` rather than a plain Docker-availability check)

Baseline lane scoping (see `tests/e2e/baseline/README.md`):

- `BAND_E2E_LANE`: The CI lane (a job: a `uv` extra + optional server/CLI setup) to scope the run to. Lane ids are content-based and decoupled from the `uv` extra — `core` (anthropic/openai-family adapters plus `copilot_sdk`, which self-downloads its CLI runtime and uses Anthropic BYOK without GitHub auth; `dev` extra), `crewai` (`dev-crewai` extra), `google` (gemini/google_adk, split out for rate-limit isolation), `backends` (codex, opencode, copilot_acp, cursor_acp, and omp_acp coding agents), `letta` (self-hosted letta server), `strands` (`dev-strands-nooa` extra), `nooa` (same `dev-strands-nooa` extra, Linux-only: it imports `fcntl`), `parlant` (`dev-parlant` extra — split from `core` because parlant's griffe/griffelib transitive deps collide with pydantic_ai's; registers no matrix adapter, a bespoke `@lane`-pinned smoke only). Resolves the lane's adapters from the registry (`ci_lanes()`, derived from each adapter's `requires`); out-of-lane adapters skip-with-reason (they're covered by their own lane) while in-lane adapters keep fail-loud (an unwired backend stays red). Unset (the local default) = full matrix, no scoping. CI never lists adapters — it derives lanes from the registry. A test's lane is derived from **all** the frameworks it touches (a matrix cell's adapter plus its `@per_adapter(peer=...)`, or a `@with_adapters` set); a test whose frameworks span more than one home lane fails collection (`assert_every_item_is_schedulable`) unless pinned with `@lane(Lane.X)` to a lane whose extra hosts them all. To add a lane, see `tests/e2e/baseline/README.md` ("Adding a CI lane").

Baseline provisioning/cleanup policy (see `tests/e2e/baseline/README.md`):

- `BAND_E2E_AUTOCLEAN`: Reap provisioned agents + rooms on teardown (default: `true`; set `false` to keep resources for debugging a failing run)
- `BAND_E2E_ORPHAN_SWEEP`: Sweep leftover agents from crashed prior runs at session start (default: `true`)
- `BAND_E2E_ORPHAN_MAX_AGE_MINUTES`: Only sweep agents older than this, so a concurrent run is never reaped mid-flight (default: `120`)
- `BAND_E2E_SCORECARD_JSON`: Write this run's adapter×test scorecard (pass/fail/skip + N/A reasons) as JSON to this path at session end (default: empty = don't emit). CI sets one path per lane; a final job merges them (see `tests/e2e/baseline/scorecard.py` and the Scorecard section of the baseline README)

## Adding a New Framework Integration

Register the new adapter and converter with the conformance infrastructure,
then run `tests/framework_conformance/` — its drift guards
(`test_config_drift.py`) fail and name whatever is still unregistered.
`tests/e2e/baseline/ADDING_AN_ADAPTER.md` covers the E2E matrix.

## Example Files (examples/ directory)

Every file under `examples/` needs PEP 723 inline script metadata at the top
(so `uv run examples/<framework>/<file>.py` works standalone), plus a handful
of other conventions (credentials via `load_agent_config`, `async with agent:
await agent.run_forever()`, etc.).

### Provisioning / setup scripts (create-agents, register-*, bootstrap)

Scripts that create remote resources (agents, rooms) or generate credential
files have failure modes plain examples don't. Follow these:

- **Persist created resource ids immediately, append-only.** Write each id to a
  cleanup ledger the moment the resource exists — before the next create or any
  file write can fail. Writing all ids once at the end orphans everything already
  created if a later step throws; overwriting the ledger on a re-run loses the
  *previous* run's ids. Both leak paid/limited resources.
- **Refuse to clobber existing credentials without an explicit `FORCE`.** A
  re-run that silently overwrites `agent_config.yaml` orphans the agents it
  referenced. Fail with a message pointing at the cleanup ledger.
- **Guard file writes against the stale bind-mount directory.** Docker
  auto-creates a *missing* bind-mount source as an empty **directory**. Check
  `path.is_dir()` / `path.exists()`, NOT `path.is_file()` (which is `False` for a
  directory and lets the work run, then crashes at `write_text` with
  `IsADirectoryError`). Do this check **before** any irreversible step (e.g.
  registering agents), so you never create resources you then can't record.
- **Keep `main()` thin.** Extract persistence (config/ledger writes) and the
  user-facing summary into small named helpers — easier to read and to test.

## Documentation Testing (markdown snippets)

Tracked `.md` files (except `examples/`) run in CI as tests via `pytest-markdown-docs`
— so `python` snippets in the docs must stay correct and runnable, not rot:

```bash
uv run pytest --markdown-docs $(git ls-files '*.md' ':!:examples/*' ':!:docs/adapters/nooa.md') --no-cov
uv run --locked --isolated --python 3.12 --all-packages --extra dev-strands-nooa pytest --markdown-docs docs/adapters/nooa.md --no-cov
```

NOOA's runnable documentation requires its legacy environment; the two commands
above cover the complete tracked documentation suite.

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
install` it and exercise the real call in this repo's venv.

## Pre-Commit Checklist

Before running the commands below, re-read your own diff once against the
`band-python-conventions` skill — including code you just wrote this session, not only
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

### Pull Request Descriptions

Use only three sections: **Why**, **What**, and **Scope**. Explain why the
change is needed, what the final implementation does, and its boundaries.
Keep the description concise and write it for a reviewer without chat context.
Do not append testing sections, checklists, progress updates, investigation
history, or repeated addenda. Keep validation evidence and unresolved failures
in the linked Linear issue or evidence attachments. The PR template follows
this format.

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
headRefOid -q .headRefOid` then fetching that commit's file content).
