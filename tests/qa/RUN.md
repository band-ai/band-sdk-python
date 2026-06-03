# How to Run the QA Test Harness

## Quick Start

```bash
# 1. Install dependencies (one-time)
uv sync --extra dev

# 2. Auto-setup: register agents + generate all credential files
python tests/qa/setup_agents.py
# Reads API keys from .env, registers agents via platform API,
# writes tests/qa/.env and all agent_config.yaml files.

# 3. Run
python tests/qa/run.py --adapter langgraph
```

**Or combine setup + run in one command:**

```bash
python tests/qa/run.py --adapter langgraph --setup
python tests/qa/run.py --all-adapters --setup
```

### Manual setup (alternative)

If you prefer to register agents manually or use existing ones:

```bash
cp tests/qa/.env.example tests/qa/.env
# Edit tests/qa/.env — fill in BAND_API_KEY_USER and LLM keys

cp tests/qa/adapters/langgraph/agent_config.yaml.example \
   tests/qa/adapters/langgraph/agent_config.yaml
# Edit and fill in agent_id + api_key for each config_key
```

## Virtual Environments

Each adapter runs its agent processes in a Python venv. Most adapters use the
default project venv (`.venv`). Adapters with conflicting dependencies use a
dedicated venv specified by the `venv:` field in their `config.yaml`.

**Setup:**

```bash
# Default venv — all adapters except parlant
uv sync --extra dev

# Parlant venv — separate due to crewai/parlant opentelemetry-sdk conflict
UV_PROJECT_ENVIRONMENT=.venv-parlant uv sync --extra dev-parlant
```

If an adapter's `venv` is not found at runtime, the runner falls back to
`uv run` and logs a warning. You can skip adapters you haven't set up.

| Venv | Adapters | Setup Command |
|------|----------|---------------|
| `.venv` (default) | langgraph, google_adk, anthropic, crewai, gemini, pydantic_ai, claude_sdk, letta | `uv sync --extra dev` |
| `.venv-parlant` | parlant | `UV_PROJECT_ENVIRONMENT=.venv-parlant uv sync --extra dev-parlant` |

## Letta: Local Server Stack

The letta example is the only adapter that needs extra services. The Letta
agent runtime runs in a **Letta server**, and it reaches Band platform tools
through a **band-mcp** MCP server. Pointing at Letta Cloud needs a
`LETTA_API_KEY` and a publicly reachable band-mcp; instead we run both locally
so letta can be tested with no external accounts.

```bash
# Brings up Letta (Docker :8283) + band-mcp (:8002), wires examples/letta/.env
tests/qa/scripts/letta_local_stack.sh up

python tests/qa/run.py --adapter letta --examples 01 --all

tests/qa/scripts/letta_local_stack.sh down   # tear down when finished
```

What `up` does:

1. Ensures the band-mcp source repo is on the band branch and synced
   (`BAND_MCP_DIR`, default `~/band/thenvoi-mcp`, branch `rename-band-mcp`).
2. Starts the Letta server in Docker (`band-qa-letta`, `letta/letta:latest`)
   with `OPENAI_API_KEY` from `tests/qa/.env`.
3. Starts band-mcp on `0.0.0.0:8002` using the letta agent's key from
   `examples/letta/agent_config.yaml` (agent scope) against `BAND_REST_URL`.
4. Writes `examples/letta/.env` so the harness points the agent at
   `LETTA_BASE_URL=http://localhost:8283` and
   `MCP_SERVER_URL=http://host.docker.internal:8002/sse`.

> **MCP URL relaxation (test-only).** The Letta *server* (in Docker) reaches
> band-mcp on the host via `host.docker.internal`. Stock `letta/letta` images
> reject private/loopback MCP targets via an SSRF guard
> (`validate_mcp_server_url`), with no env toggle — which blocks every local
> self-hosted MCP server. `up` applies a small, documented relaxation **only to
> the disposable `band-qa-letta` container** so local MCP works. It is never
> applied anywhere else; `down` discards the container entirely.

## Environment Variables

The harness loads environment variables in this order:
1. `tests/qa/.env` (QA-level defaults — platform URLs, user API key, LLM keys)
2. Adapter-specific `.env` from the adapter's `env_file` config (overrides)

**Default target: production** (`https://app.band.ai`). Override in `.env` for localhost:
```bash
BAND_REST_URL=http://localhost:4000
BAND_WS_URL=ws://localhost:4000/api/v1/socket/websocket
```

## Agent Registration

**Automatic (recommended):** `setup_agents.py` reads each adapter's
`config.yaml`, registers a platform agent for every config_key, and writes
the credentials into `agent_config.yaml` files (both `tests/qa/adapters/`
and `examples/`).

```bash
python tests/qa/setup_agents.py                # all adapters
python tests/qa/setup_agents.py --adapters langgraph,anthropic
python tests/qa/setup_agents.py --dry-run      # preview without registering
```

The script finds `BAND_API_KEY_USER` by searching `.env` and
`.env.userkey` files in the repo and its worktrees. LLM keys are read
from the repo-root `.env`.

**Manual (single agent):**
```bash
curl -X POST $BAND_REST_URL/api/v1/me/agents/register \
  -H "X-API-Key: $BAND_API_KEY_USER" \
  -H "Content-Type: application/json" \
  -d '{"agent": {"name": "QA-langgraph-simple", "description": "QA test agent"}}'
```
Save the returned `agent_id` and `api_key` into the adapter's `agent_config.yaml`.

## CLI Reference

### Single adapter

```bash
# Core scenarios (A-C) on all examples
python tests/qa/run.py --adapter langgraph

# Core scenarios on specific examples
python tests/qa/run.py --adapter langgraph --examples 01,03

# Expanded scenarios (E-I, excluding D)
python tests/qa/run.py --adapter langgraph --expanded

# Specific expanded scenarios
python tests/qa/run.py --adapter langgraph --expanded --scenarios E,G,I

# Scenario D only (multi-participant, cross-adapter)
python tests/qa/run.py --adapter langgraph --expanded --scenarios D

# Everything: core + expanded + scenario D
python tests/qa/run.py --adapter langgraph --all
```

### All adapters

```bash
# Full sweep: every adapter, all scenarios, cross-adapter summary
python tests/qa/run.py --all-adapters
```

Runs each configured adapter sequentially (core A-C, expanded E-I, scenario D).
Produces per-adapter reports and a `cross_adapter_summary.md`.

### Spawning as a subagent

```
Agent({
  description: "QA <adapter> examples",
  prompt: "Run the QA test harness for the <adapter> adapter. "
    "Run: python tests/qa/run.py --adapter <adapter> --all "
    "Then read the reports in tests/qa/reports/ and summarize results. "
    "For any FAIL or PARTIAL, read the agent logs and identify root cause.",
  mode: "auto"
})
```

For parallel multi-adapter QA, spawn one agent per adapter in a single message.

## Credential Files

| File | Purpose | Gitignored |
|------|---------|------------|
| `tests/qa/setup_agents.py` | Registers agents and generates all credential files | No (checked in) |
| `tests/qa/.env` | Platform URLs, user API key, LLM keys | Yes |
| `tests/qa/.env.example` | Template for `.env` | No (checked in) |
| `tests/qa/adapters/*/agent_config.yaml` | Agent IDs + API keys per adapter | Yes |
| `tests/qa/adapters/*/agent_config.yaml.example` | Template for agent config | No (checked in) |
| `examples/*/agent_config.yaml` | Agent IDs + API keys for examples (generated by setup) | Yes |
