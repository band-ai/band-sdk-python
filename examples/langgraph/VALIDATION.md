# LangGraph Validation Runbook

Use this runbook when updating LangGraph or LangChain dependencies. Record real results for the PR without including secrets, private paths, or internal ticket names.

## Package version audit

```bash
uv lock --check
uv run python - <<'PY'
from importlib.metadata import version
packages = [
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
]
for package in packages:
    print(f"{package}=={version(package)}")
PY
```

## Local verification

```bash
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

## Standalone LangGraph smoke tests

These commands use real LangGraph graphs outside the platform runtime.

```bash
uv run --extra langgraph python examples/langgraph/standalone_calculator.py
uv run --extra langgraph python examples/langgraph/standalone_rag.py
uv run --extra langgraph python examples/langgraph/standalone_sql_agent.py
```

## Docker smoke tests

```bash
docker compose config
docker build --target langgraph -t thenvoi-sdk-langgraph:validation .
docker compose up --build langgraph-01-simple
docker compose up --build langgraph-02-custom-tools
docker compose up --build langgraph-04-calculator
docker compose up --build langgraph-05-rag
docker compose up --build langgraph-06-sql-agent
docker compose up --build langgraph-09-research-ops
```

## Live production E2E

Run only with real production-safe demo credentials. Redact tokens and local secret locations from logs and PR text.

```bash
E2E_TESTS_ENABLED=true \
THENVOI_REST_URL="https://app.thenvoi.com" \
THENVOI_WS_URL="wss://app.thenvoi.com/api/v1/socket/websocket" \
uv run pytest tests/e2e/adapters/ -k langgraph -v -s --no-cov
```

Manual live examples:

```bash
uv run --extra langgraph python examples/langgraph/05_rag_as_tool.py
uv run --extra langgraph python examples/langgraph/06_delegate_to_sql_agent.py
uv run --extra langgraph python examples/langgraph/09_research_ops_orchestrator.py
```

If the model endpoint is OpenAI-compatible but not direct OpenAI, describe it as an OpenAI-compatible hosted endpoint/model.

## Results

| Check | Command | Environment | Result | Timestamp | Notes |
|---|---|---|---|---|---|
| Package audit | `uv lock --check` and version script | local | PASS | 2026-05-06 | Resolved latest stable package family listed above. |
| Adapter/converter/integration tests | focused pytest command | local | PASS | 2026-05-06 | 58 passed, 1 upstream deprecation warning. |
| Lint | `uv run ruff check .` | local | PASS | 2026-05-06 | All checks passed. |
| Type check | `uv run pyrefly check` | local | PASS | 2026-05-06 | 0 errors. |
| Standalone calculator | `standalone_calculator.py` | local runtime | PASS | 2026-05-06 | Addition, multiplication, and divide-by-zero path exercised. |
| Standalone RAG | direct `create_rag_graph()` invoke | direct OpenAI key for embeddings/chat | PASS | 2026-05-06 | Built vector store and returned `AIMessage` answer about reward hacking. OpenAI-compatible hosted endpoint separately returned retryable HTTP 502 for embeddings. |
| Standalone SQL | `standalone_sql_agent.py` | OpenAI-compatible hosted endpoint | PASS | 2026-05-06 | HTTP 200 model calls; listed tables, Employee columns, and employee count. |
| Docker config | `docker compose config` | Docker | PASS | 2026-05-06 | Compose renders all LangGraph services, including research ops. |
| Production E2E | `tests/e2e/adapters/ -k langgraph` | production | PASS | 2026-05-06 | 2 passed, 10 deselected; REST auth and WebSocket agent runtime exercised. |
| Live RAG example | `05_rag_as_tool.py` | production | NOT RUN | 2026-05-06 | Direct graph smoke passed; long-running room example still needs a manual trigger if required. |
| Live SQL example | `06_delegate_to_sql_agent.py` | production | COVERED BY COMPLEX SMOKE | 2026-05-06 | Production nested-graph smoke used SQL graph-as-tool and platform send-message. |
| Live operations orchestrator | `09_research_ops_orchestrator.py` | production | NOT RUN | 2026-05-06 | Example compiles; production E2E and previous complex nested graph smoke cover the same adapter/tool surfaces. |

## Production evidence fields

Fill these in for the PR body after each live run.

- REST URL: `https://app.thenvoi.com`
- WebSocket URL: `wss://app.thenvoi.com/api/v1/socket/websocket`
- Agent config key: demo LangGraph agent
- Model label: `gpt-5.4-mini` through an OpenAI-compatible hosted endpoint
- Prompt sent: E2E smoke prompt and tool-execution prompt from `tests/e2e/adapters/test_all_adapters.py`
- Observed platform response: agent replied through `thenvoi_send_message`; E2E assertions passed
- Observed LangGraph event sequence: adapter emitted platform-visible message/tool behavior during E2E run; focused tests assert `on_tool_start` -> `tool_call` and `on_tool_end` -> `tool_result`
- Observed HTTP status codes or WebSocket event names: REST agent identity succeeded; WebSocket agent room subscription succeeded; model chat completions returned HTTP 200 in standalone SQL smoke
- Response shape summary: SQL smoke returned final `AIMessage.content` strings for table list, Employee schema, and employee count
- Skipped checks and exact blockers: RAG direct graph invocation hit retryable HTTP 502 from the hosted embeddings endpoint at 2026-05-06T20:53:17Z; live manual long-running examples still need room-trigger evidence if required for PR body

## Adversarial review checklist

- Custom/static graph receives adapter prompt through config.
- `MessagesState` graphs get one bootstrap system prompt per room, not duplicates on reconnect.
- Same-name tool calls replay with matched args and tool result ids.
- `graph_as_tool()` preserves parent config fields while overriding only subgraph thread id.
- Contact and memory tools are capability-gated.
- Public docs and PR text do not include internal ticket names, private paths, secret names, or false provider claims.
- Validation evidence is real server/tool/event output, not only mocks or docs assertions.
