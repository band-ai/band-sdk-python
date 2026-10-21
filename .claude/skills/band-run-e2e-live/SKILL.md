---
name: band-run-e2e-live
description: Use this skill when the user wants to RUN the band-sdk-python E2E baseline tests against the live platform + real LLMs (not write them) — "run the e2e tests live", "run the usage smoke for anthropic", "validate this adapter live", "run all lanes", "why does the codex/opencode lane skip/fail", "set up .env.test", "switch the venv for crewai", "run crewai and the dev lanes at the same time / in parallel". Covers the venv-per-lane dependency split (including `uv run --isolated` for running lanes simultaneously), .env.test, BAND_E2E_LANE scoping, per-lane commands, and the backends (codex/opencode) auth/CWD/server gotchas. For AUTHORING new E2E tests use band-write-e2e-test instead; for diagnosing a failing test use band-investigate-test-error.
---

# Run the E2E Live Tests

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
   anything. A tracked `.env.test.example` documents the vars. Minimum for any run:
   `BAND_API_KEY_USER` + the provider key(s) for the adapters in the lane.

## Venv per lane — the dependency-conflict split (the #1 gotcha)

crewai, parlant, and Strands/NOOA are kept out of the default venv, so there are
**four** uv extras and a single `.venv` you switch between:

| Extra | Command | Contains | Lanes it serves |
|---|---|---|---|
| `dev` | `uv sync --extra dev` | everything **except crewai, parlant, strands and nooa** (anthropic, claude_sdk, copilot_sdk, agno, langgraph, pydantic_ai, gemini, google_adk, ACP, opencode, letta) | core, google, backends, letta |
| `dev-crewai` | `uv sync --extra dev-crewai` | crewai + test tooling only (**no** pydantic-ai/parlant/agno/gemini/...) | crewai |
| `dev-strands-nooa` | `uv sync --extra dev-strands-nooa` | Strands, NOOA (Python 3.12–3.13, off Windows) and their LangGraph peer, on OpenAI 2 | strands, nooa |
| `dev-parlant` | `uv sync --extra dev-parlant` | parlant + test tooling | parlant |

- **Why the split exists:** `pyproject.toml` declares the extras as `[tool.uv] conflicts`,
  so uv resolves each side in its own fork. Without the declaration uv *could* still
  resolve crewai with the dev side — don't: that collapses the forks and holds the
  whole lock at crewai's exact versions (it pins pydantic `<2.13` and exact `~=` minors
  of mcp, aiofiles, regex and tomli). parlant and pydantic-ai install two distributions
  (`griffe`/`griffelib`) into the same `griffe` import path, and Strands/NOOA need
  OpenAI 2 while modern Pydantic AI needs OpenAI 3.
- **Switching mutates the single `.venv`** — run `uv sync --extra <extra>` and it
  installs/uninstalls to match. After a non-`dev` run, **`uv sync --extra dev` to
  restore** the default before running anything else (unit suite, other lanes). This is
  fine for sequential work but *serializes* crewai against every dev lane. To run them
  **at the same time**, use `uv run --isolated` instead — see "Running lanes
  simultaneously" below.
- **Detect which env you're in** before running:
  ```bash
  uv run python -c "import crewai" 2>/dev/null && echo dev-crewai || echo "not crewai env"
  uv run python -c "import pydantic_ai" 2>/dev/null && echo dev || echo "not dev env"
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
E2E_TESTS_ENABLED=true BAND_E2E_LANE=core   uv run pytest tests/e2e/baseline/ -v --no-cov
E2E_TESTS_ENABLED=true BAND_E2E_LANE=google uv run pytest tests/e2e/baseline/ -v --no-cov

# A single file / cell (fast iteration):
E2E_TESTS_ENABLED=true BAND_E2E_LANE=core uv run pytest \
  "tests/e2e/baseline/smoke/inspection/test_usage.py::test_usage_recorded_for_a_turn[anthropic]" -v --no-cov

# crewai lane — switch venv first, restore after:
uv sync --extra dev-crewai
E2E_TESTS_ENABLED=true BAND_E2E_LANE=crewai uv run pytest tests/e2e/baseline/ -v --no-cov
uv sync --extra dev

# Just collect (verify parametrization/lane wiring, no live calls):
E2E_TESTS_ENABLED=true uv run pytest tests/e2e/baseline/ --collect-only -q --no-cov
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
E2E_TESTS_ENABLED=true BAND_E2E_LANE=core   uv run pytest tests/e2e/baseline/ -v --no-cov

# Terminal B — crewai lane, its own isolated env:
E2E_TESTS_ENABLED=true BAND_E2E_LANE=crewai \
  uv run --locked --isolated --python 3.12 --all-packages --extra dev-crewai \
  pytest tests/e2e/baseline/ -v --no-cov
```

- The dev lanes (`core`, `google`, `backends`, `letta`) all share `dev`, so they can run
  concurrently against the **same** `.venv` — an isolated env is only needed for the
  lanes on a conflicting extra.

### All lanes at once

Because separate pytest processes only *read* the venv, every lane can run as its own
concurrent process — the `dev` lanes share `.venv`, each other lane gets its isolated
env:

```bash
export E2E_TESTS_ENABLED=true
# dev lanes — one background process each, all sharing .venv:
for L in core google backends; do
  BAND_E2E_LANE=$L uv run pytest tests/e2e/baseline/ -v --no-cov \
    > "/tmp/e2e-$L.log" 2>&1 &
done
# crewai — concurrent, in its own isolated env:
BAND_E2E_LANE=crewai \
  uv run --locked --isolated --python 3.12 --all-packages --extra dev-crewai \
  pytest tests/e2e/baseline/ -v --no-cov \
  > /tmp/e2e-crewai.log 2>&1 &
wait   # block until all lanes finish; then grep the logs for the summaries
```

Two things temper "all lanes", none of them venv-related:

- **`letta` and `backends` need local infra** — letta a dockerized Letta server (its
  own section below), backends codex API-key login + a running opencode server (next
  section). Without it those lanes **fail-loud** — drop them from the loop until set
  up (add `letta` to the `for` loop once its server is running).
- **Shared-provider rate limits** — `core` and `crewai` (and `strands`) **all** hit
  OpenAI, so running them together multiplies OpenAI pressure and can throttle;
  `google` is split out precisely for this isolation. If you hit 429s, run `core` and
  `crewai` sequentially and keep `google` (and `backends`) parallel. Band-account
  contention is fine (unique agent names per lane; orphan sweep has an age guard).

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

- **codex** (`setup-codex.sh`):
  ```bash
  npm install -g @openai/codex @agentclientprotocol/codex-acp   # pin the versions setup-codex.sh pins
  printenv OPENAI_API_KEY | codex login --with-api-key   # API-key auth — see below
  codex login status
  ```
  Then in `.env.test`: `CODEX_CWD=<disposable dir outside the repo>` (a **stable**
  directory — not `/tmp/...`, which clears on reboot and fails the `is a dir` gate) and
  `E2E_CODEX_CWD_IS_DISPOSABLE=true`. `CODEX_MODEL` defaults to `OPENAI_MODEL`
  (`src/band/core/defaultmodels.py`).
  - **Auth is the #1 codex trap:** `codex login status`. If it says **"Logged in using
    ChatGPT"**, switch to exactly what the script does — **API-key** login
    (`codex login --with-api-key` from `OPENAI_API_KEY`), not the interactive ChatGPT
    login. Re-login with the API key to switch.
- **opencode** (`setup-opencode.sh`):
  ```bash
  npm install -g opencode-ai   # pin the version setup-opencode.sh pins
  # write ~/.config/opencode/opencode.json with the Zen provider key + a free model
  # (small_model opencode/mimo-v2.6-flash-free); see the script for the exact JSON
  cd "$(mktemp -d)" && opencode serve --hostname 127.0.0.1 --port 4096 &   # empty cwd!
  curl -fsS http://127.0.0.1:4096/global/health                            # wait until healthy
  ```
  Then in `.env.test`: `OPENCODE_BASE_URL=http://127.0.0.1:4096`, plus
  `E2E_OPENCODE_BASH_ASKS=true` **if** the config you wrote gates `bash` to `ask`
  (`"permission": {"bash": "ask"}`, as the CI script sets). The manual-approval
  smoke (`smoke/behavior/approvals/test_approvals.py`) needs a real
  `permission.asked` and fails naming that reason without it — the server's rules,
  not the adapter's `approval_mode`, decide when one is raised. Serve from an
  **empty** dir — in the repo checkout a weak free model wanders into the source
  instead of replying. Uses `OPENCODE_ZEN_API_KEY` (Zen free tier).
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
  E2E_TESTS_ENABLED=true BAND_E2E_LANE=backends uv run python -c "
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
OPENAI_API_KEY=... GITHUB_ENV=/dev/null .github/scripts/setup-letta.sh
```

Then in `.env.test` (or exported):

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
E2E_TESTS_ENABLED=true BAND_E2E_LANE=letta uv run pytest tests/e2e/baseline/ -v --no-cov
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
values, no folding: whether `input_tokens` includes cache is provider-specific, so
`input_tokens + cache_read_tokens + cache_write_tokens` is the robust total prompt size
(`TurnUsage` in `src/band/core/types.py`).

## Quick recipe: validate a change across all runnable lanes, then commit

1. `uv sync --extra dev` → full unit suite + `pyrefly check` + `ruff check`.
2. `BAND_E2E_LANE=core` then `google` live (dev).
3. `uv sync --extra dev-crewai` → `BAND_E2E_LANE=crewai` live → `uv sync --extra dev`.
   Same for `strands`/`nooa` (`dev-strands-nooa`) and `parlant` (`dev-parlant`).
   (Or, to run steps 2 and 3 in parallel, use `uv run --locked --isolated` per "Running
   lanes simultaneously" — no back-and-forth `uv sync`.)
4. `backends` (on `dev`): set up via the equivalents of the `.github/scripts/setup-*.sh`
   scripts above (API-key codex login, running opencode server, `.env.test`
   entries), then `BAND_E2E_LANE=backends`. `letta` (on `dev`): start the docker Letta
   server per "letta lane", then `BAND_E2E_LANE=letta`. If either isn't set up, report
   it runs in CI's own lane.
5. Commit/push only when green (and only if the user asked).
