# Agno adapter — E2E scenarios

Live, multi-agent E2E tests for the Agno adapter. They run real Agno agents
against a real Band platform with a real LLM and assert on platform state via
**direct REST queries** (not just WebSocket observation).

> The generic smoke / tool-execution coverage for Agno already runs via the
> parametrized suite in `tests/e2e/adapters/test_all_adapters.py`. This folder
> covers behavior that suite can't: multi-agent orchestration, history
> rehydration across restarts, and reasoning-as-thoughts emission.

## What each test verifies

| Test | Flow | Key assertion |
|------|------|---------------|
| `test_multi_agent.py::…invites_calculator_for_total` | Assistant (A) chats about a grocery list, invites a calculator agent (B), asks for the total, then removes B. | B's `add_numbers` tool actually ran (REST), total reported, B removed. |
| `test_multi_agent.py::…survives_restart[A/B/both]` | Same flow, but an agent is killed and restarted mid-conversation. | The restarted agent rehydrates history (`is_session_bootstrap`) and continues; tool runs again. |
| `test_thoughts.py::…emits_thought_events` | A single `reasoning=True` agent answers a step-by-step question. | A `thought` event is emitted (verified via REST). |

## Cast

- **Agent A — assistant** (`build_assistant_adapter`): no tools of its own, but
  gets Band's chat/participant tools by default. Orchestrates B.
- **Agent B — calculator** (`create_calculator_agno_adapter`): owns a native
  `add_numbers` tool; `Emit.EXECUTION` posts its `tool_call`/`tool_result` so
  the run is observable.
- **User**: sends the trigger messages and observes via WebSocket.

## Prerequisites

Set in `.env.test` (tests `skip` cleanly if missing):

- `BAND_API_KEY`, `TEST_AGENT_ID` — agent A
- `BAND_API_KEY_2`, `TEST_AGENT_ID_2` — agent B (must be discoverable by A)
- `BAND_API_KEY_USER` — the user/observer
- `ANTHROPIC_API_KEY` — the LLM
- `E2E_TESTS_ENABLED=true`

## Run

```bash
# Whole folder (use --log-cli-level=INFO to watch the transcript live)
E2E_TESTS_ENABLED=true uv run pytest tests/e2e/scenarios/agno/ -v -s --no-cov --log-cli-level=INFO

# One restart variant
E2E_TESTS_ENABLED=true uv run pytest \
  "tests/e2e/scenarios/agno/test_multi_agent.py::TestAgnoMultiAgent::test_multi_agent_survives_restart[A]" \
  -v -s --no-cov --log-cli-level=INFO
```

`-s` is required to see the Rich step transcript; `--log-cli-level=INFO`
streams the per-message logs (only shown on failure otherwise).

## Findings baked into these tests

- **Events are observed via REST, not WebSocket.** Agent-emitted events
  (`thought`, `tool_call`, `tool_result`) are returned by `agent_api_context`
  but are **not** delivered over the user's WebSocket `message_created` stream
  (that carries only `text`). So the tests **synchronize on the agent's `text`
  reply over WS, then assert events via REST** (`fetch_all_context`).
- **WebSocket reconnect is rate-limited (HTTP 429).** Restart scenarios stop and
  start the same agent rapidly, which the platform throttles "after a recent
  supersede." `running_agent` retries the connect with tenacity, honoring the
  server-supplied `retry_after`. Running the whole folder in one shot may pause
  for these cooldowns.
- **`reasoning=True` is fragile with Band tools.** Anthropic's stricter
  reasoning-mode tool validation can reject a Band tool schema (an `integer`
  with `maximum`), emptying the reasoning step. The agent still answers; the
  thought is asserted via REST. Tests carry `@flaky(reruns=2)` for LLM
  nondeterminism.

## Layout

- `conftest.py` — adapter builders, grocery fixture data, REST assertion
  helpers, the `running_agent` lifecycle (with tenacity reconnect retry), and
  room fixtures.
- `test_multi_agent.py` — Scenarios 1 & 2.
- `test_thoughts.py` — Scenario 3.
