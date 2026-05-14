# Parlant validation report

Date: 2026-05-14
Branch: `fix/parlant-testing`
Base: `dev`

## Scope

I validated the Parlant integration from the SDK adapter down through examples and test surfaces. The goal was to determine whether the Parlant examples actually work and whether the implementation makes sense for Band's peer-room, @mention-driven collaboration model.

This pass covered the isolated Parlant dependency environment, adapter/tool/converter tests, the generic example runner, standalone Parlant examples, live E2E tests against production Band, and a simplify/review pass over the diff.

## Executive result

The Parlant adapter is structurally coherent, the two existing Parlant E2E tests now pass against production Band with a real OpenAI provider, and every standalone Parlant example now has live proof: each script started as its actual example file, connected to production Band, received a live user @mention, and produced a platform-observed response. This required explicit environment normalization. A fresh shell that only sources `/Users/pp/thenvoi/.env` does **not** provide `OPENAI_API_KEY`; the provider key used for live proof was loaded from an approved local env file after proving it was missing from the sourced workspace env.

The original examples were not all correct. `examples/run_agent.py --example parlant` used a removed adapter API, `examples/parlant/03_support_agent.py` relied on Parlant's Emcie default instead of the OpenAI provider used by the other Parlant examples, and the Parlant E2E harness could be broken by wrong Band credential precedence, fixed local Parlant ports, unbounded teardown, and hidden flaky reruns.

Those issues are fixed on `fix/parlant-testing`.

## What changed

### `examples/run_agent.py`

The generic Parlant runner now starts a real Parlant server with `p.NLPServices.openai`, creates Parlant tools through `create_parlant_tools()`, creates a Parlant agent, attaches guidelines, and passes the live `server` and `parlant_agent` into `ParlantAdapter`.

Before the fix, the runner failed at construction:

```text
ParlantAdapter.__init__() got an unexpected keyword argument 'model'
```

The simplify pass also caught that `--model` and `--streaming` were misleading for Parlant. The runner now warns and ignores `--model` because Parlant's provider setup controls model behavior, and warns/ignores `--streaming` because `ParlantAdapter.SUPPORTED_EMIT` is empty. It also avoids passing an emit-only `AdapterFeatures` object into `create_parlant_tools()`, which had accidentally removed contact tools.

### `examples/parlant/03_support_agent.py`

The support example now uses `p.NLPServices.openai`, documents that `OPENAI_API_KEY` is required, creates Parlant platform tools, and attaches those tools to the escalation guideline. Escalation now maps to the actual Band handoff model: lookup peer, add participant, then @mention with context.

### `tests/e2e/adapters/test_parlant.py`

The Parlant E2E harness now:

- normalizes process-local Band credentials when a sourced env exposes a user key as `THENVOI_API_KEY`;
- preserves that user key as `THENVOI_API_KEY_USER` for user-scoped trigger messages;
- loads the local `tom_agent` config as the agent-scoped `THENVOI_API_KEY` / `TEST_AGENT_ID` when needed;
- uses OpenAI explicitly and skips cleanly when `OPENAI_API_KEY` is absent;
- allocates isolated localhost ports for every Parlant server/tool-service instance instead of sharing `8800`/`8818`;
- removes Parlant-specific flaky rerun markers that overrode `--reruns 0`;
- bounds Parlant server teardown with a 30-second timeout so a passed smoke test is not converted into a fixture-cleanup failure.

The Parlant tool E2E no longer uses the generic helper prompt because that prompt begins with an @mention to the agent. Parlant interpreted that self-mention as the reply target and correctly refused to reply to an agent handle that was not a room participant. The Parlant-specific test now asks the agent to reply to the actual user participant while still waking the agent with the platform mention.

## Validation evidence

### Dependency environment

```bash
uv sync --extra dev-parlant
```

Result: succeeded.

Installed Parlant version observed locally:

```text
parlant_dist_version= 3.3.1
```

### Changed-file syntax and constructor check

```bash
uv run python -m py_compile examples/run_agent.py examples/parlant/01_basic_agent.py examples/parlant/02_with_guidelines.py examples/parlant/03_support_agent.py examples/parlant/04_tom_agent.py examples/parlant/05_jerry_agent.py
```

Result: succeeded.

The obsolete `ParlantAdapter(model=...)` call is gone.

### Fresh provider key proof

A shell that only sources `/Users/pp/thenvoi/.env` and unsets proxy OpenAI endpoints does not provide an OpenAI key:

```bash
set -a && source /Users/pp/thenvoi/.env && set +a
unset OPENAI_BASE_URL OPENAI_API_BASE
python3 - <<'PY'
import os
print(f'OPENAI_API_KEY_present={bool(os.getenv("OPENAI_API_KEY"))}')
print(f'OPENAI_API_KEY_shape={"sk" if os.getenv("OPENAI_API_KEY", "").startswith("sk-") else "unset_or_nonstandard"}')
print(f'OPENAI_BASE_URL_present={bool(os.getenv("OPENAI_BASE_URL"))}')
print(f'OPENAI_API_BASE_present={bool(os.getenv("OPENAI_API_BASE"))}')
PY
```

Observed:

```text
OPENAI_API_KEY_present=False
OPENAI_API_KEY_shape=unset_or_nonstandard
OPENAI_BASE_URL_present=False
OPENAI_API_BASE_present=False
```

I then loaded an approved local OpenAI key source without printing the secret:

```text
OPENAI_API_KEY_present=True
OPENAI_API_KEY_shape=sk
OPENAI_KEY_SOURCE=approved local env file outside this repo
OPENAI_BASE_URL_present=False
OPENAI_API_BASE_present=False
```

### Live E2E failures found before final proof

A run using an inherited shell `OPENAI_BASE_URL` failed at the provider layer, not the Band adapter layer:

```text
OPENAI_BASE_URL=https://codex.ppflix.net/backend-api/codex
openai.PermissionDeniedError: <html> ... id="challenge-error-text">Enable JavaScript and cookies to continue ...
/backend-api/codex/chat/completions
cZone: 'chatgpt.com'
/cdn-cgi/challenge-platform/
```

That endpoint is a ChatGPT/Codex backend behind a Cloudflare challenge. The final live commands explicitly unset `OPENAI_BASE_URL` and `OPENAI_API_BASE`.

A fresh run after sourcing `/Users/pp/thenvoi/.env` exposed wrong Band credential precedence before Parlant started:

```text
403 This endpoint requires agent authentication
/api/v1/agent/me
```

That proved the E2E environment could expose a user-scoped Band key as `THENVOI_API_KEY`. The Parlant E2E module now normalizes this process-local credential wiring before the E2E fixtures construct the agent-scoped REST client.

A later tool-execution run reached Parlant but failed because the generic helper prompt caused Parlant to treat its own @mention as the reply target:

```text
Expected at least one message to contain 'PINEAPPLE'
Got: "I can't reply to @[[agent-id]] because that handle isn't listed as a participant."
```

That was fixed by making the Parlant-specific E2E prompt target the user participant while still waking the agent through the platform mention.

### Final live E2E proof

Both final live tests were run from a shell that sourced `/Users/pp/thenvoi/.env`, explicitly unset `OPENAI_BASE_URL`/`OPENAI_API_BASE`, verified an `sk-` OpenAI provider key was wired from an approved local env file, used production Band URLs, and ran with `E2E_TIMEOUT=300`, `--timeout=300`, and `--reruns 0`. Secrets were not printed.

Smoke response test:

```bash
set -a && source /Users/pp/thenvoi/.env && set +a
unset OPENAI_BASE_URL OPENAI_API_BASE
# export OPENAI_API_KEY from an approved local env file outside this repo without printing it
THENVOI_BASE_URL=https://app.thenvoi.com \
THENVOI_REST_URL=https://app.thenvoi.com \
E2E_TESTS_ENABLED=true \
E2E_TIMEOUT=300 \
uv run pytest tests/e2e/adapters/test_parlant.py::TestParlantE2E::test_smoke_responds_to_message -v -s --no-cov --timeout=300 --reruns 0
```

Result:

```text
OPENAI_API_KEY_present=True
OPENAI_API_KEY_shape=sk
OPENAI_KEY_SOURCE=approved local env file outside this repo
OPENAI_BASE_URL_present=False
OPENAI_API_BASE_present=False
Initialized OpenAIService
Server is ready to accept requests.
PASSED
1 passed in 43.43s
```

Tool execution test:

```bash
set -a && source /Users/pp/thenvoi/.env && set +a
unset OPENAI_BASE_URL OPENAI_API_BASE
# export OPENAI_API_KEY from an approved local env file outside this repo without printing it
THENVOI_BASE_URL=https://app.thenvoi.com \
THENVOI_REST_URL=https://app.thenvoi.com \
E2E_TESTS_ENABLED=true \
E2E_TIMEOUT=300 \
uv run pytest tests/e2e/adapters/test_parlant.py::TestParlantE2E::test_tool_execution_send_message -v -s --no-cov --timeout=300 --reruns 0
```

Result:

```text
OPENAI_API_KEY_present=True
OPENAI_API_KEY_shape=sk
OPENAI_KEY_SOURCE=approved local env file outside this repo
OPENAI_BASE_URL_present=False
OPENAI_API_BASE_present=False
Initialized OpenAIService
Server is ready to accept requests.
PASSED
1 passed in 47.13s
```

This proves the current Parlant adapter can start with a real OpenAI provider, connect to production Band, receive a live room message, and send a platform-observed response. The tool execution test also proves the Parlant tool bridge can satisfy the `PINEAPPLE` assertion when the prompt targets the actual user participant.

### Standalone example proof

After registering the missing local `parlant_agent` and `support_agent` entries with the Human API and storing the returned one-time agent keys only in ignored local `agent_config.yaml`, I ran every standalone Parlant example as its actual script. Each run used production Band URLs, an `sk-` OpenAI provider key from an approved local env file outside this repo, and `OPENAI_BASE_URL` / `OPENAI_API_BASE` unset. For each script, the proof harness waited for the example process to start, created a live Band room for that configured agent, added the user participant, sent a user-authored @mention to the example agent, and observed a text response over the user WebSocket.

Sanitized result:

```text
OPENAI_API_KEY_present=True
OPENAI_API_KEY_shape=sk
OPENAI_BASE_URL_present=False
OPENAI_API_BASE_present=False
EXAMPLE_PROOF example=01_basic_agent.py config=parlant_agent openai_initialized=True agent_started=True agent_id_present=True room_id_present=True response_observed=True response_contains_token=True
EXAMPLE_PROOF example=02_with_guidelines.py config=parlant_agent openai_initialized=True agent_started=True agent_id_present=True room_id_present=True response_observed=True response_contains_token=True
EXAMPLE_PROOF example=03_support_agent.py config=support_agent openai_initialized=True agent_started=True agent_id_present=True room_id_present=True response_observed=True response_contains_token=True
EXAMPLE_PROOF example=04_tom_agent.py config=tom_agent openai_initialized=True agent_started=True agent_id_present=True room_id_present=True response_observed=True response_contains_token=True
EXAMPLE_PROOF example=05_jerry_agent.py config=jerry_agent openai_initialized=True agent_started=True agent_id_present=True room_id_present=True response_observed=True response_contains_token=True
```

This proves the standalone Parlant examples are not just syntactically valid: they start with OpenAI-backed Parlant, connect to production Band as real agents, process live @mentions, and send observable Band messages.

### Final local checks

```bash
uv run ruff check examples/run_agent.py examples/parlant/03_support_agent.py tests/e2e/adapters/test_parlant.py
uv run ruff format --check examples/run_agent.py examples/parlant/03_support_agent.py tests/e2e/adapters/test_parlant.py
uv run pytest tests/adapters/test_parlant_adapter.py tests/converters/test_parlant.py tests/integrations/parlant/test_tools.py tests/framework_conformance/test_adapter_conformance.py tests/framework_conformance/test_converter_conformance.py -k parlant -v --no-cov
```

Latest result:

```text
All checks passed!
3 files already formatted
89 passed, 4 skipped, 232 deselected
```

### Drift tests

A broader drift command still has an existing isolated-environment issue:

```bash
uv run pytest tests/framework_conformance/test_config_drift.py tests/framework_conformance/test_tool_name_drift.py -v --no-cov
```

Result:

```text
25 passed, 2 skipped, 1 failed
```

The failure is `TestAdapterConfigDrift::test_all_adapter_modules_are_covered`, reporting missing conformance configs for non-Parlant adapters (`anthropic`, `gemini`, `langgraph`, `pydantic_ai`) because their optional dependencies are intentionally absent in the `dev-parlant` environment. The Parlant tool-name drift checks passed. I did not change this because it is not a Parlant example/runtime bug.

## Product-fit assessment

The Parlant adapter makes sense for Band as a policy/guideline-driven participant in a Band room. Band remains the collaboration substrate: identity, @mention routing, rooms, audit, participants, contacts, and platform tools. Parlant controls the behavior of one participant after Band wakes it.

The strongest fit is customer-support-style flows. Parlant guidelines can decide when to answer, ask clarifying questions, or escalate. Band supplies the peer handoff: `lookup_peers`, `add_participant`, and a message that @mentions the specialist with context.

The integration would be misleading if positioned as Parlant replacing Band's coordination model. It does not. Parlant decides what its agent should do once invoked; Band still owns who wakes up, what room context is visible, and how handoffs happen.

## Remaining limits

The standalone proof creates persistent live Band rooms because there is no room-delete endpoint in this SDK flow. I did not include room IDs, agent IDs, API keys, or exact local credential-store paths in this durable report.

## Conclusion

The adapter-level implementation is coherent, the targeted Parlant test layer passes, the two live Parlant E2E tests pass against production Band with a real OpenAI provider after explicit provider-key wiring, and all five standalone Parlant example scripts now have live end-to-end proof. The examples and harness needed real fixes, and those fixes are on `fix/parlant-testing`.
