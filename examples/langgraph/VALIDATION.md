# LangGraph validation

Use this runbook when updating the LangGraph integration. The goal is to prove the SDK works with current packages, runnable examples, and live Band production traffic. Keep this file public-safe: do not add API keys, private file paths, secret names, or internal ticket references.

## Package audit

Run:

```bash
python - <<'PY'
import json, pathlib, re, urllib.request
packages = [
    "langgraph",
    "langgraph-sdk",
    "langgraph-checkpoint",
    "langgraph-prebuilt",
    "langchain",
    "langchain-core",
    "langchain-openai",
    "langchain-anthropic",
]
lock = pathlib.Path("uv.lock").read_text()
for pkg in packages:
    data = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=20))
    latest = data["info"]["version"]
    match = re.search(rf'^name = "{re.escape(pkg)}"\nversion = "([^"]+)"', lock, re.M)
    resolved = match.group(1) if match else "MISSING"
    print(f"{pkg}: resolved={resolved} pypi_latest={latest}")
PY
```

Current verified package set:

| Package | Locked version | PyPI latest at validation |
|---|---:|---:|
| langgraph | 1.1.10 | 1.1.10 |
| langgraph-sdk | 0.3.14 | 0.3.14 |
| langgraph-checkpoint | 4.0.3 | 4.0.3 |
| langgraph-prebuilt | 1.0.13 | 1.0.13 |
| langchain | 1.2.17 | 1.2.17 |
| langchain-core | 1.3.3 | 1.3.3 |
| langchain-openai | 1.2.1 | 1.2.1 |
| langchain-anthropic | 1.4.3 | 1.4.3 |

## Local verification commands

```bash
uv lock --check
uv run pytest tests/adapters/test_langgraph_adapter.py tests/converters/test_langchain.py tests/integrations/langgraph/ -v
uv run pytest tests/framework_conformance/test_adapter_conformance.py tests/framework_conformance/test_tool_name_drift.py tests/framework_conformance/test_config_drift.py tests/test_smoke.py -v
uv run ruff check .
uv run pyrefly check
```

## Standalone example commands

```bash
uv run --extra langgraph python examples/langgraph/standalone_calculator.py
uv run --extra langgraph python examples/langgraph/standalone_rag.py
uv run --extra langgraph python examples/langgraph/standalone_sql_agent.py
```

`standalone_rag.py` and `standalone_sql_agent.py` require a valid hosted model API key because they use `ChatOpenAI`.

## Docker validation commands

```bash
docker compose config --quiet
docker build --target langgraph -t thenvoi-sdk-langgraph:validation .
docker compose up --build langgraph-01-simple
docker compose up --build langgraph-02-custom-tools
docker compose up --build langgraph-04-calculator
docker compose up --build langgraph-05-rag
docker compose up --build langgraph-06-sql-agent
```

## Live production validation commands

Run the E2E adapter suite with production URLs and real credentials:

```bash
E2E_TESTS_ENABLED=true \
THENVOI_BASE_URL="https://app.thenvoi.com" \
THENVOI_REST_URL="https://app.thenvoi.com" \
THENVOI_WS_URL="wss://app.thenvoi.com/api/v1/socket/websocket" \
uv run pytest tests/e2e/adapters/ -k langgraph -v -s --no-cov
```

For a lower-level smoke that removes hosted model availability as a variable, run a deterministic LangChain chat model through the real `LangGraphAdapter` against production. It must still use current `langchain.agents.create_agent`, current LangGraph event streaming, Band REST, Band WebSocket, and the real `thenvoi_send_message` tool.

Expected live evidence:

- REST `GET /api/v1/agent/me`: 200
- REST user profile lookup: 200
- REST chat creation: 200
- REST participant add: 200
- REST user trigger message: 200
- WebSocket room subscription receives an Agent `message_created` event
- Persisted room history includes `tool_call`, `tool_result`, and Agent `text`

## Validation results

| Check | Command | Environment | Result | Timestamp | Notes |
|---|---|---|---|---|---|
| Package audit | PyPI JSON audit above | local network + `uv.lock` | PASS | 2026-05-06 | All audited LangGraph/LangChain packages matched latest stable PyPI versions. |
| LangGraph runtime tests | `uv run pytest tests/adapters/test_langgraph_adapter.py tests/converters/test_langchain.py tests/integrations/langgraph/ -v` | local | PASS | 2026-05-06 | 54 passed; one LangChain pending deprecation warning from `langgraph.cache.base`. |
| Conformance/lint/type checks | `uv lock --check && uv run pytest tests/framework_conformance/test_adapter_conformance.py tests/framework_conformance/test_tool_name_drift.py tests/framework_conformance/test_config_drift.py tests/test_smoke.py -v && uv run ruff check . && uv run pyrefly check` | local | PASS | 2026-05-06 | 185 passed, 8 skipped, ruff passed, pyrefly reported 0 errors. |
| Standalone calculator | `uv run --extra langgraph python examples/langgraph/standalone_calculator.py` | local | PASS | 2026-05-06 | Produced `5 + 3 = 8`, `7 * 6 = 42`, and expected divide-by-zero error. |
| Standalone SQL | `uv run --extra langgraph python examples/langgraph/standalone_sql_agent.py` | local | BLOCKED | 2026-05-06 | Requires a valid `OPENAI_API_KEY`; no valid hosted model key was available in the local environment used for this run. |
| Docker compose config | `docker compose config --quiet` | local Docker | PASS | 2026-05-06 | Compose config is valid; Docker warned that `OPENAI_API_KEY` was unset. |
| Live production E2E with hosted model | `uv run pytest tests/e2e/adapters/ -k langgraph -v -s --no-cov` | production Band + local hosted model key | BLOCKED | 2026-05-06 | Band prod credentials worked after selecting a valid agent, but available hosted model keys failed authentication. |
| Live production deterministic LangGraph smoke | inline deterministic LangChain chat model through `LangGraphAdapter` | production Band REST/WS | PASS | 2026-05-06 | See evidence below. |

## Live production evidence

Production endpoints used:

- REST: `https://app.thenvoi.com`
- WebSocket: `wss://app.thenvoi.com/api/v1/socket/websocket`

Observed server and event sequence from the deterministic live smoke on 2026-05-06:

- `GET /api/v1/agent/me`: 200
- user profile lookup: 200
- create chat room: 200
- add user participant to room: 200
- send user trigger message: 200
- list room messages after run: 200
- WebSocket received one Agent message containing validation token `LIVE_LANGGRAPH_VALIDATION_e5c9530a`
- Persisted message sequence included:
  - User `text`: trigger message mentioning the agent
  - Agent `text`: `LIVE_LANGGRAPH_VALIDATION_e5c9530a`
  - Agent `tool_call`: `thenvoi_send_message` with input containing the validation token and a user mention
  - Agent `tool_result`: `thenvoi_send_message` result containing a created message id and recipient metadata

This proves the current LangChain/LangGraph agent factory, LangGraph `astream_events(..., version="v2")`, SDK tool event persistence, Band production REST, Band production WebSocket, and the real platform `thenvoi_send_message` tool all worked together in one live run.

## Adversarial review notes

Concerns checked for this update:

- Stale dependency claim: dismissed because the lockfile versions were compared directly against PyPI JSON and matched latest stable releases.
- Mock-only adapter proof: dismissed because the test suite now includes a real compiled LangGraph tool-event smoke test, and production validation exercised the real adapter against Band REST/WS.
- Docs-only test padding: valid concern. The docs-drift test was removed; example validation is handled by running examples and recording evidence instead.
- Tool event replay drift: dismissed because converter tests cover both older string repr outputs and structured tool output shapes.
- `graph_as_tool()` config injection drift: dismissed because direct tests cover isolated and shared thread modes using injected `RunnableConfig`.
- Hosted model availability: still a separate operational dependency. The production platform path was validated with a deterministic LangChain model; hosted OpenAI-backed examples should be rerun when a valid provider key is available.
