# LangGraph validation runbook

Use this runbook when changing the LangGraph adapter, LangChain tool wrappers, graph-as-tool utilities, or LangGraph examples. Keep copied results public-safe: no API keys, private paths, internal issue IDs, or secret locations.

## Package version audit

Record the resolved versions from `uv.lock` and compare them with current stable PyPI releases:

```bash
python - <<'PY'
import json
import urllib.request
from pathlib import Path

packages = {
    "langgraph",
    "langgraph-sdk",
    "langgraph-checkpoint",
    "langgraph-prebuilt",
    "langchain",
    "langchain-core",
    "langchain-openai",
    "langchain-anthropic",
    "langchain-community",
    "langchain-text-splitters",
}
resolved = {}
current = None
for line in Path("uv.lock").read_text().splitlines():
    if line.startswith("name = "):
        current = line.split("=", 1)[1].strip().strip('"')
    elif line.startswith("version = ") and current in packages:
        resolved[current] = line.split("=", 1)[1].strip().strip('"')

for package in sorted(packages):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/json", timeout=20) as response:
        latest = json.load(response)["info"]["version"]
    print(f"{package}: resolved={resolved.get(package)} latest={latest}")
PY
```

## Local checks

```bash
uv lock --check
uv run pytest \
  tests/adapters/test_langgraph_adapter.py \
  tests/converters/test_langchain.py \
  tests/integrations/langgraph/ \
  tests/framework_conformance/test_adapter_conformance.py \
  tests/framework_conformance/test_tool_name_drift.py \
  tests/framework_conformance/test_config_drift.py \
  tests/test_smoke.py \
  -v
uv run ruff check .
uv run pyrefly check
```

## Standalone LangGraph examples

These prove the graphs compile and run with the resolved package set. They require model credentials for examples that call an LLM.

```bash
uv run --extra langgraph python examples/langgraph/standalone_calculator.py
uv run --extra langgraph python examples/langgraph/standalone_rag.py
uv run --extra langgraph python examples/langgraph/standalone_sql_agent.py
```

## Docker checks

```bash
docker compose config
docker build --target langgraph -t thenvoi-sdk-langgraph:validation .
docker compose up --build langgraph-01-simple
docker compose up --build langgraph-02-custom-tools
docker compose up --build langgraph-03-custom-personality
docker compose up --build langgraph-04-calculator
docker compose up --build langgraph-05-rag
docker compose up --build langgraph-06-sql-agent
docker compose up --build langgraph-09-research-ops
```

## Live production checks

Use environment variables and local config files for credentials. Do not print or paste secret values.

```bash
E2E_TESTS_ENABLED=true \
THENVOI_BASE_URL="https://app.thenvoi.com" \
THENVOI_REST_URL="https://app.thenvoi.com" \
THENVOI_WS_URL="wss://app.thenvoi.com/api/v1/socket/websocket" \
uv run pytest tests/e2e/adapters/ -k langgraph -v -s --no-cov
```

Run at least one nontrivial live example that forces tool or subgraph activity:

```bash
uv run --extra langgraph python examples/langgraph/05_rag_as_tool.py
uv run --extra langgraph python examples/langgraph/06_delegate_to_sql_agent.py
uv run --extra langgraph python examples/langgraph/09_research_ops_orchestrator.py
```

## Results table

| Check | Command | Environment | Result | Timestamp | Notes |
|---|---|---|---|---|---|
| Package audit |  | Local |  |  | Record resolved and latest versions |
| Focused LangGraph tests |  | Local |  |  | Include pass count and warnings |
| Full local verification |  | Local |  |  | Include exact skipped checks, if any |
| Standalone graph smoke |  | Local model endpoint |  |  | Record example names, not secrets |
| Docker compose config |  | Docker |  |  | Record services validated |
| Live E2E |  | Production |  |  | Record sanitized event sequence |
| Live complex example |  | Production |  |  | Record prompt and response shape |

## Latest validation run

Captured on 2026-05-06 against the current lockfile.

| Check | Command | Environment | Result | Timestamp | Notes |
|---|---|---|---|---|---|
| Package audit | PyPI JSON audit script | Local + PyPI | Passed | 2026-05-06 | Resolved versions matched latest stable PyPI versions for the LangGraph/LangChain package set |
| Final focused LangGraph tests | `uv run pytest tests/adapters/test_langgraph_adapter.py tests/converters/test_langchain.py tests/integrations/langgraph/test_graph_tools.py tests/integrations/langgraph/test_langchain_tools.py -v` | Local | Passed | 2026-05-06 | 59 passed, 1 upstream LangGraph pending deprecation warning |
| Requested local validation suite | `uv run pytest tests/adapters/test_langgraph_adapter.py tests/converters/test_langchain.py tests/integrations/langgraph/ tests/framework_conformance/test_adapter_conformance.py tests/framework_conformance/test_tool_name_drift.py tests/framework_conformance/test_config_drift.py tests/test_smoke.py -v` | Local | Passed | 2026-05-06 | 244 passed, 8 skipped, 8 warnings |
| Lint | `uv run ruff check .` | Local | Passed | 2026-05-06 | All checks passed |
| Type check | `uv run pyrefly check` | Local | Passed | 2026-05-06 | 0 errors |
| Standalone calculator | `uv run --extra langgraph python examples/langgraph/standalone_calculator.py` | Local | Passed | 2026-05-06 | Produced expected 8, 42, and divide-by-zero result |
| Standalone SQL graph | `uv run --extra langgraph python examples/langgraph/standalone_sql_agent.py` | OpenAI-compatible endpoint, `gpt-5.4-mini` | Passed | 2026-05-06 | HTTP 200 chat completions; listed tables, Employee columns, and employee count |
| Standalone RAG graph | inline `create_rag_graph().ainvoke(...)` smoke | OpenAI-compatible endpoint, `gpt-5.4-mini` | Passed | 2026-05-06 | Returned final `AIMessage` for reward-hacking prompt |
| Docker compose config | `docker compose config --services` | Docker | Passed | 2026-05-06 | Service discovery/config only; listed all LangGraph services, including `langgraph-09-research-ops` |
| Live LangGraph E2E smoke | `uv run pytest tests/e2e/adapters/test_all_adapters.py -k 'langgraph and smoke' -v -s --no-cov` | Production + OpenAI-compatible endpoint, `gpt-5.4-mini` | Passed | 2026-05-06 | 1 passed |
| Live LangGraph E2E tool execution | `uv run pytest tests/e2e/adapters/test_all_adapters.py -k 'langgraph and tool_execution' -v -s --no-cov` | Production + OpenAI-compatible endpoint, `gpt-5.4-mini` | Passed | 2026-05-06 | 1 passed after one rerun |
| Live complex orchestrator | custom harness using `09_research_ops_orchestrator.py` graph factory | Production + OpenAI-compatible endpoint, `gpt-5.4-mini` | Passed | 2026-05-06 | Created production room, sent user mention, observed persisted tool/result/text events through REST polling |

Live complex event evidence:

```text
REST URL: https://app.thenvoi.com
WS URL: wss://app.thenvoi.com/api/v1/socket/websocket
Room ID: 189d1ce8-eb2c-4920-8b13-f4d53a74b1e9
Trigger message ID: 2cc8ce31-0b84-48fc-a583-68f7920c77ad
Agent name: Claude Code
Model label: gpt-5.4-mini via OpenAI-compatible endpoint
Prompt: @Claude Code Plan briefly, calculate 37 * 19, ask the sample database how many employees exist, and send the final answer.
Final text: @[[72528895-17d2-41f0-83ef-64d5f5968e0f]] 37 × 19 = 703. The sample music store database has 8 employees.
Event sequence: tool_call -> thought -> tool_result -> tool_call -> tool_call -> tool_result -> tool_call -> tool_result -> tool_call -> tool_result -> tool_call -> tool_result -> tool_result -> tool_call -> text -> tool_result
HTTP/WebSocket evidence: standalone SQL validation logged OpenAI-compatible HTTP/1.1 200 OK chat completion calls; production REST polling returned persisted message records from the room above.
```

Representative persisted production messages:

| inserted_at UTC | message_id | type | tool/event | run_id | observed input/output |
|---|---|---|---|---|---|
| 2026-05-06 22:16:44.179891 | `163984b8-2126-4d6e-8182-95a946630adc` | tool_call | `thenvoi_send_event` / `on_tool_start` | `019dff5d-6543-7d51-961b-60edd1f90dbb` | input: `message_type='thought'`, content: `Plan: I’ll first compute 37 × 19...` |
| 2026-05-06 22:16:44.189806 | `31d991c7-2ed8-4454-bf7d-5b018f2013de` | thought | platform event | n/a | `Plan: I’ll first compute 37 × 19, then query the sample music store database for the employee count...` |
| 2026-05-06 22:16:45.299419 | `1436cae9-9710-40a0-95ee-66dcc83148b8` | tool_call | `calculator_math` / `on_tool_start` | `019dff5d-69af-7d23-84b1-85e0ec444ab1` | input: `{'operation': 'multiply', 'a': 37, 'b': 19}` |
| 2026-05-06 22:16:45.495645 | `ea5eabf5-923b-4950-ae93-98fd6d91dcc4` | tool_result | `calculator_math` / `on_tool_end` | `019dff5d-69af-7d23-84b1-85e0ec444ab1` | output: `content='703' name='calculator_math' tool_call_id='call_vTq8bpr1MqwkRBYb9AmbeaRO'` |
| 2026-05-06 22:16:45.392208 | `a887ed74-f735-4cd5-85a6-abaaf69ecb3c` | tool_call | `database_assistant` / `on_tool_start` | `019dff5d-69af-7d23-84b1-85ff849fd71b` | input: `How many employees exist in the sample music store database?` |
| 2026-05-06 22:16:46.416120 | `cdb88d74-2b21-4b93-ba1e-f83575a1301e` | tool_call | `sql_db_list_tables` / `on_tool_start` | `019dff5d-6e10-71d3-884b-a2fc1204a066` | input: `{}` |
| 2026-05-06 22:16:46.549454 | `c11b1f7d-c651-4439-b4de-444a704f0df1` | tool_result | `sql_db_list_tables` / `on_tool_end` | `019dff5d-6e10-71d3-884b-a2fc1204a066` | output includes `Album, Artist, Customer, Employee, Genre, Invoice, InvoiceLine, MediaType, Playlist, PlaylistTrack, Track` |
| 2026-05-06 22:16:47.083159 | `50fd4b46-1d7c-4d3d-80a8-36f1da44fddb` | tool_call | `sql_db_query_checker` / `on_tool_start` | `019dff5d-706c-7570-8312-31e92d367d51` | input: `SELECT COUNT(*) AS employee_count FROM Employee;` |
| 2026-05-06 22:16:48.969348 | `e2cb2279-b185-4f95-8ff8-913b4bcf25c7` | tool_call | `sql_db_query` / `on_tool_start` | `019dff5d-7806-7422-af48-e0a3c4783ac5` | input: `SELECT COUNT(*) AS employee_count FROM Employee;` |
| 2026-05-06 22:16:49.092996 | `a0e42b86-898a-482a-9f3d-7e2459035aa6` | tool_result | `sql_db_query` / `on_tool_end` | `019dff5d-7806-7422-af48-e0a3c4783ac5` | output: `content='[(8,)]' name='sql_db_query' ... tool_call_id='call_ojE15Ap99N4wUzDRbXnhbwko'` |
| 2026-05-06 22:16:49.478902 | `f600b339-7f7f-4cc7-b156-7b0a30b96a43` | tool_result | `database_assistant` / `on_tool_end` | `019dff5d-69af-7d23-84b1-85ff849fd71b` | output: `There are **8 employees** in the sample music store database.` |
| 2026-05-06 22:16:50.257860 | `322d1bd7-5cb9-4145-9003-b15860f27b0b` | tool_call | `thenvoi_send_message` / `on_tool_start` | `019dff5d-7d07-7cd3-99e4-898e9dd5fc0d` | input: `@darv 37 × 19 = 703. The sample music store database has 8 employees.` |
| 2026-05-06 22:16:50.421445 | `ba5dfadb-bf20-412c-bcc3-62041f732c21` | text | final platform message | n/a | `@[[72528895-17d2-41f0-83ef-64d5f5968e0f]] 37 × 19 = 703. The sample music store database has 8 employees.` |
| 2026-05-06 22:16:50.519178 | `d9cf75d7-444d-453b-b021-57d660a1d50d` | tool_result | `thenvoi_send_message` / `on_tool_end` | `019dff5d-7d07-7cd3-99e4-898e9dd5fc0d` | output includes `success=True`, recipient handle `darv`, and final message id `ba5dfadb-bf20-412c-bcc3-62041f732c21` |

## Production evidence fields

Copy sanitized evidence into the PR body when live validation is run.

```text
REST URL: https://app.thenvoi.com
WS URL: wss://app.thenvoi.com/api/v1/socket/websocket
Agent config label: <example key only, no credential values>
Model label: <model name or OpenAI-compatible endpoint label, no tokens>
Prompt: <prompt sent to the live room>
Observed final response: <brief response summary or redacted content>
Tool event sequence: message_created:tool_call -> message_created:tool_result -> message_created:text
HTTP/WebSocket evidence: <status codes, Phoenix event names, or response shape>
Notes: <blockers or unusual behavior>
```

Example sanitized event shape:

```json
[
  {
    "event": "message_created",
    "message_type": "tool_call",
    "sender_type": "Agent",
    "content_shape": {
      "event": "on_tool_start",
      "name": "thenvoi_send_event",
      "run_id": "redacted",
      "data": {"input": "object"}
    }
  },
  {
    "event": "message_created",
    "message_type": "tool_result",
    "sender_type": "Agent",
    "content_shape": {
      "event": "on_tool_end",
      "name": "thenvoi_send_event",
      "run_id": "redacted",
      "data": {"output": "object|string"}
    }
  },
  {
    "event": "message_created",
    "message_type": "text",
    "sender_type": "Agent"
  }
]
```

## Live behavioral round-trip (post-refactor)

Captured against `https://app.thenvoi.com` on commit `923035c`
(the THENVOI_SYSTEM_PROMPT_CONFIG_KEY removal). These exercise the real
post-refactor `LangGraphAdapter.on_message` path against production:
real REST + real `create_agent` graph + real OpenAI LLM + real
`AgentTools` (no mocks anywhere).

### Round-trip 1 — bootstrap turn

- room_id: `8b319c00-cea6-4abf-8e52-b52fdd57e93c`
- rendered system prompt length: 1787 chars
  (`contains_agent_name=True`, `contains_custom=True`)
- adapter chose `thenvoi_send_event` (one mention-free event)
- platform agent items observed via REST `fetch_room_context` (3):
  - `tool_call` → `on_tool_start thenvoi_send_event`
  - `thought`   → `"Hello everyone! I'm here to assist you with your queries and tasks."`
  - `tool_result` → `on_tool_end thenvoi_send_event` (success=True)
- result: `LIVE_E2E: SUCCESS – LangGraph adapter produced a real production message/event`

### Round-trip 2 — bootstrap + follow-up turn (checkpointer carry-forward)

- room_id: `306ac939-3049-44dd-b57a-6465a06b6232`
- after turn 1 (bootstrap): checkpointer `total=5, system=1, system_len=1727`
- after turn 2 (`is_session_bootstrap=False`): `total=9, system=1`
  (the adapter does NOT prepend a second system message; the checkpointer
  carries the original SystemMessage forward — exactly the invariant the
  refactor restored)
- platform agent `thought` events observed on the same room (2):
  - `"Hello, everyone! I'm here to assist you with anything you need."`
  - `"Hey there, fabulous people! 🎉 I'm super excited to be here and ready to help you out!"`
- result: `LIVE_FOLLOWUP: SUCCESS — bootstrap-once + checkpointer carry-forward verified live`

What this proves end-to-end against production:

1. `on_started` renders the system prompt from `prompt_template` /
   `custom_section` / agent metadata.
2. `on_message` prepends exactly one `("system", rendered_prompt)` on
   the first turn per room and never again.
3. The simple-pattern `create_agent` graph compiles, the LLM picks a
   platform tool on its own, and the tool reaches REST.
4. The LangGraph `InMemorySaver` checkpointer carries the SystemMessage
   forward across turns — every model call sees exactly one SystemMessage,
   matching the convention used by every other Band adapter.
5. The platform actually persists the agent's output, observable via
   `AgentTools.fetch_room_context`.
