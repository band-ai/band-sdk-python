---
name: band-run-e2e-live
description: Use this skill when the user wants to RUN the band-sdk-python E2E baseline tests against the live platform + real LLMs (not write them) — "run the e2e tests live", "run the usage smoke for anthropic", "validate this adapter live", "run all lanes", "why does the codex/opencode lane skip/fail", "set up .env.test", "switch the venv for crewai", "run crewai and the dev lanes at the same time / in parallel". Covers the venv-per-lane dependency split (including `uv run --isolated` for running lanes simultaneously), .env.test, BAND_E2E_LANE scoping, per-lane commands, and the backends (codex/opencode) auth/CWD/server gotchas. For AUTHORING new E2E tests use band-write-e2e-test instead; for diagnosing a failing test use band-investigate-test-error.
---

# Run the E2E Live Tests

Read [the shared workflow runtime](../workflow-runtime.md) before execution.
Use its ownership, process cleanup, and reporting rules for every run.

Operational playbook for executing `tests/e2e/baseline/` against the **real** Band
platform + real LLM providers. The tests provision their own agents/rooms and make
real LLM calls, so they need credentials and, for some lanes, a specific venv or
external binaries/servers. Everything here is about *running* — to author tests use
`band-write-e2e-test`; to debug a failure use `band-investigate-test-error`.

## The two things that make a live run work

1. **`E2E_TESTS_ENABLED=true`** — the only switch that skips the whole suite. Without
   it, every item under `tests/e2e/**` skips (that's also why markdown-doc snippets
   under `tests/e2e/**` never execute in CI).
2. **`.env.test`** in the repo root — gitignored, local-only, **auto-loaded** by the
   baseline settings (`load_dotenv(ENV_TEST_FILE, override=False)` in
   `tests/e2e/baseline/settings.py`, so a value already exported in your shell wins).
   Holds the Band keys, provider keys, models, and URLs. You do **not** export
   credentials for pytest, but shell values take precedence: check for stale
   overrides and align the run with the repo-root file without printing secrets.
   Backend setup processes must load that file explicitly. A tracked `.env.test.example` documents the vars. Minimum for any run:
   `BAND_API_KEY_USER` + the provider key(s) for the adapters in the lane.

## Venv per lane — the dependency-conflict split (the #1 gotcha)

crewai, parlant, and Strands/NOOA are kept out of the default venv, so there are
**four** uv extras and a single `.venv` you switch between:

| Extra | Command | Contains | Lanes it serves |
|---|---|---|---|
| `dev` | `uv sync --locked --all-packages --extra dev` | everything **except crewai, parlant, strands and nooa** (anthropic, claude_sdk, copilot_sdk, agno, langgraph, pydantic_ai, gemini, google_adk, ACP, opencode, letta) | core, google, backends, letta |
| `dev-crewai` | `uv sync --locked --all-packages --extra dev-crewai` | crewai + test tooling only (**no** pydantic-ai/parlant/agno/gemini/...) | crewai |
| `dev-strands-nooa` | `uv sync --locked --all-packages --extra dev-strands-nooa` | Strands, NOOA (Python 3.12–3.13, off Windows) and their LangGraph peer, on OpenAI 2 | strands, nooa |
| `dev-parlant` | `uv sync --locked --all-packages --extra dev-parlant` | parlant + test tooling | parlant |

- **Why the split exists:** `pyproject.toml` declares the extras as `[tool.uv] conflicts`,
  so uv resolves each side in its own fork. Without the declaration uv *could* still
  resolve crewai with the dev side — don't: that collapses the forks and holds the
  whole lock at crewai's exact versions (it pins pydantic `<2.13` and exact `~=` minors
  of mcp, aiofiles, regex and tomli). parlant and pydantic-ai install two distributions
  (`griffe`/`griffelib`) into the same `griffe` import path, and Strands/NOOA need
  OpenAI 2 while modern Pydantic AI needs OpenAI 3.
- **Switching mutates the single `.venv`** — run `uv sync --locked --all-packages --extra <extra>` and it
  installs/uninstalls to match. After a non-`dev` run, **`uv sync --locked --all-packages --extra dev` to
  restore** the default before running anything else (unit suite, other lanes). This is
  fine for sequential work but *serializes* crewai against every dev lane. To run them
  **at the same time**, use `uv run --isolated` instead — see "Running lanes
  simultaneously" below.
- **Detect which env you're in** before running:
  ```bash
  uv run --all-packages python -c "import crewai" 2>/dev/null && echo dev-crewai || echo "not crewai env"
  uv run --all-packages python -c "import pydantic_ai" 2>/dev/null && echo dev || echo "not dev env"
  ```
- Symptoms of the wrong env: `ModuleNotFoundError: No module named 'agno'` (you're in
  dev-crewai, running a dev-lane test) or crewai import errors (you're in dev). The
  **unit suite** (`tests/adapters/test_usage_mapping.py` etc.) needs `dev`; crewai unit
  tests mock crewai via `sys.modules` so they run in either env.
- **pyrefly** runs clean in `dev` even on `src/band/adapters/crewai.py`:
  `ignore-missing-imports` in `pyproject.toml` treats crewai/parlant/strands/nooa as
  `Any` there; their real type coverage comes from their own lane's venv.

## Lanes and how to scope a run

`BAND_E2E_LANE=<lane>` scopes the run to one CI lane: out-of-lane cells
**skip-with-reason** (covered by their own lane), in-lane cells stay **fail-loud** (a
missing key/backend goes red — intended). Unset = full matrix. A test's lane is derived
from the frameworks it touches; a test spanning >1 lane fails collection unless pinned
with `@lane(Lane.X)` (that's a `band-write-e2e-test` concern).

| Lane | Env | Adapters | Extra creds/infra |
|---|---|---|---|
| `core` | dev | anthropic, claude_sdk, agno, langgraph, pydantic_ai, copilot_sdk | ANTHROPIC + OPENAI keys (copilot_sdk self-downloads its CLI runtime; Anthropic BYOK, no GitHub auth) |
| `google` | dev | gemini, google_adk (split out for rate-limit isolation) | GOOGLE/GEMINI key |
| `crewai` | **dev-crewai** | crewai, crewai_flow | OPENAI key |
| `backends` | dev | codex, opencode, copilot_acp, cursor_acp, omp_acp | see gotchas below |
| `letta` | dev | letta (matrix fan; Linux-only in CI) | docker Letta server + OPENAI key — see "letta lane" below |
| `strands` | **dev-strands-nooa** | strands | OPENAI key |
| `nooa` | **dev-strands-nooa** | nooa | ANTHROPIC key; Python 3.12–3.13 on POSIX; Linux-only in CI |
| `parlant` | **dev-parlant** | none — a bespoke `@lane(Lane.PARLANT)`-pinned smoke, no matrix adapter | OPENAI key; the smoke runs its own in-process Parlant server |

## Running — copy-paste

```bash
# One lane (from the matching venv):
E2E_TESTS_ENABLED=true BAND_E2E_LANE=core   uv run --all-packages pytest tests/e2e/baseline/ -v --no-cov
E2E_TESTS_ENABLED=true BAND_E2E_LANE=google uv run --all-packages pytest tests/e2e/baseline/ -v --no-cov

# A single file / cell (fast iteration):
E2E_TESTS_ENABLED=true BAND_E2E_LANE=core uv run --all-packages pytest \
  "tests/e2e/baseline/smoke/inspection/test_usage.py::test_usage_recorded_for_a_turn[anthropic]" -v --no-cov

# crewai lane — switch venv first, restore after:
uv sync --locked --all-packages --extra dev-crewai
E2E_TESTS_ENABLED=true BAND_E2E_LANE=crewai uv run --all-packages pytest tests/e2e/baseline/ -v --no-cov
uv sync --locked --all-packages --extra dev

# Just collect (verify parametrization/lane wiring, no live calls):
E2E_TESTS_ENABLED=true uv run --all-packages pytest tests/e2e/baseline/ --collect-only -q --no-cov
```

- Always `--no-cov`. Live cells take ~10–60s each (real provisioning + LLM) — give
  generous command timeouts (a full lane can run minutes).
- To validate the SDK across lanes end-to-end: run `core` + `google` on `dev`, then
  switch to `dev-crewai` for `crewai`. `backends` usually needs local setup (below).

## Running lanes simultaneously

Switching the single `.venv` back and forth means crewai and the dev lanes can never
run at once, and each switch tears down the other lane's env. To run them in
**parallel**, run each non-`dev` lane with
`uv run --locked --isolated --python 3.12 --all-packages --extra <extra>`, so one lane
never overwrites another lane's environment — `.venv` stays the `dev` env, no
`uv sync` in between:

```bash
# Terminal A — dev lanes, default .venv:
E2E_TESTS_ENABLED=true BAND_E2E_LANE=core   uv run --all-packages pytest tests/e2e/baseline/ -v --no-cov

# Terminal B — crewai lane, its own isolated env:
E2E_TESTS_ENABLED=true BAND_E2E_LANE=crewai \
  uv run --locked --isolated --python 3.12 --all-packages --extra dev-crewai \
  pytest tests/e2e/baseline/ -v --no-cov
```

- The dev lanes (`core`, `google`, `backends`, `letta`) all share `dev`, so they can run
  concurrently against the **same** `.venv` — an isolated env is only needed for the
  lanes on a conflicting extra.

### Covering all lanes

Derive the current lanes and their extras from `toolkit/ci_lanes.py`, the
registry, and `.github/workflows/e2e.yml`; the table above is an overview.
Run every lane supported by the host, including Strands, NOOA, and Parlant
in their matching isolated environments. Report unsupported or unconfigured
lanes explicitly rather than treating a partial run as full coverage.

Track each launched process and log separately, await each exit status, and
read its pytest summary. A shell `wait` without individual statuses does
not establish that every lane passed. Clean up processes and servers started
by this run, including when a lane fails or is interrupted.

- **`letta` and `backends` need local infrastructure** described below.
- **Shared-provider rate limits** can make parallel runs counterproductive:
  core, CrewAI, and Strands all use OpenAI. Serialize those lanes when needed.
  Provisioning uses unique agent names and an age guard for orphan sweeps.

## backends lane — use the setup scripts

The canonical setup is **`.github/scripts/setup-codex.sh`**,
**`.github/scripts/setup-opencode.sh`**, `setup-copilot.sh`, `setup-cursor.sh` and
`setup-omp.sh` (invoked by the `backends` job in `.github/workflows/e2e.yml`, gated on
`if: matrix.lane == 'backends'`). Read them first — they are the source of truth for
what this lane needs. They install the CLIs, authenticate/serve, and export
`CODEX_CWD` / `E2E_CODEX_CWD_IS_DISPOSABLE` / `OPENCODE_BASE_URL` /
`E2E_OPENCODE_BASH_ASKS` to later steps via **`$GITHUB_ENV`** (CI-only; **not** GitHub
secrets, and the scripts won't run verbatim locally because `$GITHUB_ENV` is unset).
Locally, do the equivalent:

- **codex** (`setup-codex.sh`): use the pinned CLI versions in that script.
  Inspect the installed CLI's auth/config options before setup. Reuse suitable
  existing auth, or use a verified process-scoped credential/state mechanism
  for the test process; do not run the CI script's global login replacement
  against a developer's personal profile. Load provider keys from `.env.test`
  without printing them. If isolated auth cannot be configured, report that
  setup requirement before changing the user's saved login.
  In `.env.test`, set `CODEX_CWD` to an existing disposable directory outside
  the repository and `E2E_CODEX_CWD_IS_DISPOSABLE=true`. A temporary directory
  is valid for a run when recreated and cleaned up by that run; a saved path
  must still exist. The model default comes from `core/defaultmodels.py`.
- **opencode** (`setup-opencode.sh`): use the script's pinned version and
  config JSON, but put `opencode.json` only in this run's disposable server
  working directory (the script verifies this placement across platforms).
  Do not overwrite `~/.config/opencode/opencode.json`. Load
  `OPENCODE_ZEN_API_KEY` from `.env.test` into the server process, start it
  from that directory on a free loopback port, and track its process handle
  and log. Poll `/global/health` with a bounded deadline before testing;
  terminate and await the owned server during cleanup.
  Set `OPENCODE_BASE_URL` to that server and declare
  `E2E_OPENCODE_BASH_ASKS=true` only when its `bash` permission is `ask`.
  The manual-approval smoke needs a real `permission.asked`; server rules,
  rather than adapter `approval_mode`, determine whether it is raised.
- **copilot_acp / cursor_acp / omp_acp** (`setup-copilot.sh`, `setup-cursor.sh`,
  `setup-omp.sh`): copilot_acp is Anthropic BYOK — the gate is the `copilot` CLI plus
  `ANTHROPIC_API_KEY`, no login; cursor_acp needs the `agent` CLI plus
  `CURSOR_API_KEY` (CI) or a local `agent login`; omp_acp needs Bun ≥ `OMP_MIN_BUN`, a
  working `omp`, and a provider key for `OMP_MODEL`.
- The backends lane also runs the editor-facing ACP path
  (`tests/integrations/acp/test_e2e_codex_acp.py`, against the installed `codex-acp`)
  after the baseline run.
- Verify the requirement gate without spending an LLM call:
  ```bash
  E2E_TESTS_ENABLED=true BAND_E2E_LANE=backends uv run --all-packages python -c "
  from tests.e2e.baseline.settings import BaselineSettings
  from tests.e2e.baseline.toolkit.deps import Dep, requirement_reason
  s = BaselineSettings()
  for d in (Dep.CODEX_CLI, Dep.CODEX_CWD, Dep.OPENCODE_SERVER, Dep.COPILOT_CLI, Dep.CURSOR_CLI, Dep.OMP):
      r = requirement_reason(d, s); print(d.value, 'OK' if r is None else f'MISSING -> {r}')
  "
  ```

## letta lane — dockerized Letta server, self-hosted MCP

Letta runs the `@per_adapter` matrix fan plus its own smokes
(`smoke/adapters/test_letta.py`). The adapter self-hosts its Band MCP server
**inside the pytest process**; the dockerized Letta calls back into it via
`host.docker.internal` — so the only infra to stand up is the Letta server.
**Use the CI script, not a bare `docker run`** — besides the host-gateway alias
and health-wait it patches Letta's MCP URL guard inside the container (Letta
rejects MCP hostnames resolving to non-public IPs; the docker-host callback is
one, and without the patch tool discovery returns zero tools and every cell
fails):

```bash
# Load OPENAI_API_KEY from the repo-root .env.test into this process first.
GITHUB_ENV=/dev/null .github/scripts/setup-letta.sh
```

The script owns a container named `letta-server`; check for an existing container
before starting it and do not replace another run's container. Remove only the
container this run created after testing, including on failed setup.

Then in `.env.test`:

- `LETTA_BASE_URL=http://localhost:8283` — **required**; the settings default is
  Letta Cloud, and `Dep.LETTA` would then demand a `LETTA_API_KEY`.
- `OPENAI_API_KEY` — required for a self-hosted server (it makes the Letta
  server's own LLM calls; `Dep.LETTA` gates on the key for the provider
  `LETTA_MODEL`/`LETTA_EMBEDDING` name — OpenAI by default).
- Leave `MCP_SERVER_URL` **unset** — setting it switches the builder to an
  external band-mcp instead of the self-hosted server.
- `LETTA_MCP_ADVERTISED_HOST` defaults to `host.docker.internal` (right for the
  docker run above); set `127.0.0.1` only for a natively-run Letta.

```bash
E2E_TESTS_ENABLED=true BAND_E2E_LANE=letta uv run --all-packages pytest tests/e2e/baseline/ -v --no-cov
```

Gotcha: agents are created with `LETTA_EMBEDDING` (default
`openai/text-embedding-3-small`) — Letta's docker server rejects agent create
without an embedding model.

## Fail-loud vs skip (how to read a result)

- Only `E2E_TESTS_ENABLED` unset → clean skip of the whole suite.
- A missing dep in an **in-lane** cell **fails** with a reason naming the exact env
  var/CLI/server (e.g. `Codex CLI not found on PATH`, `OPENCODE_BASE_URL not set`) —
  that's "not wired here", not a code bug.
- Out-of-lane cells skip-with-reason. So no single local run turns the whole matrix
  green; scope by lane and read reds as "this backend isn't set up locally".

## Observability note

Agent-emitted events (tool_call, thought, task, usage) surface via the **REST context /
messages API** the toolkit reads, not the user WebSocket stream. Usage rides a `task`
event's `metadata.band_usage` today (a `usage` message_type isn't accepted yet), so
`capture.usage()` filters on that discriminator. Token counts are each provider's raw
values, no folding: whether `input_tokens` already includes cache is provider-specific
(Anthropic excludes it, OpenAI/Gemini include it), so
`input_tokens + cache_read_tokens + cache_write_tokens` is an upper bound on prompt
size, not an exact total — compare it within one provider, never across providers
(`TurnUsage` in `src/band/core/types.py`).

## Quick recipe: validate a change across all runnable lanes, then commit

1. Select local checks from `AGENTS.md` "Pre-Commit Checklist"; prepare the
   matching environment before executing the requested live lanes.
2. `BAND_E2E_LANE=core` then `google` live (dev).
3. `uv sync --locked --all-packages --extra dev-crewai` → `BAND_E2E_LANE=crewai` live → `uv sync --locked --all-packages --extra dev`.
   Same for `strands`/`nooa` (`dev-strands-nooa`) and `parlant` (`dev-parlant`).
   (Or, to run steps 2 and 3 in parallel, use `uv run --locked --isolated` per "Running
   lanes simultaneously" — no back-and-forth `uv sync`.)
4. `backends` (on `dev`): set up via the equivalents of the `.github/scripts/setup-*.sh`
   scripts above (API-key codex login, running opencode server, `.env.test`
   entries), then `BAND_E2E_LANE=backends`. `letta` (on `dev`): start the docker Letta
   server per "letta lane", then `BAND_E2E_LANE=letta`. If either is not set up, report
   it as unverified locally; only current CI evidence establishes its CI result.
5. Report exact commands, pass/fail/skip totals, and any unrun lanes. Commit/push
   only when authorized and the required checks pass.
