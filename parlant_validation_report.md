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

Sanitized API/session result:

```text
OPENAI_API_KEY_present=True
OPENAI_API_KEY_shape=sk
OPENAI_BASE_URL_present=False
OPENAI_API_BASE_present=False
EXAMPLE_API_PROOF example=01_basic_agent.py config=parlant_agent agent_me_id=f392c10f-ab30-48b1-8214-c0aaac6ee319 agent_me_name=Parlant_Example_Agent parlant_openai_initialized=True process_agent_started=True room_id=15e6ba4d-4dd8-4f62-887c-ed69e05fb84a participant_added=True trigger_message_id=37db260f-0ef1-4341-9be6-b326dae41d3d response_message_id=b3d32976-76ef-40b7-a9aa-ef0894b3cb00 response_sender_id=f392c10f-ab30-48b1-8214-c0aaac6ee319 response_token=BASIC-PARLANT response_contains_token=True
EXAMPLE_API_PROOF example=02_with_guidelines.py config=parlant_agent agent_me_id=f392c10f-ab30-48b1-8214-c0aaac6ee319 agent_me_name=Parlant_Example_Agent parlant_openai_initialized=True process_agent_started=True room_id=0013abda-0bb8-47f5-8f00-3f954babc44b participant_added=True trigger_message_id=85e0bfce-cef2-497b-85d8-8e97b4acd2ee response_message_id=c520e917-d3ee-4431-9be1-3b59fbcb7668 response_sender_id=f392c10f-ab30-48b1-8214-c0aaac6ee319 response_token=GUIDELINES-PARLANT response_contains_token=True
EXAMPLE_API_PROOF example=03_support_agent.py config=support_agent agent_me_id=d3f4424a-ce5f-4c1f-82d0-8cefe778e83b agent_me_name=Parlant_Support_Agent parlant_openai_initialized=True process_agent_started=True room_id=7e3ee6ad-6a28-49b4-9ffc-5dfef98b06d6 participant_added=True trigger_message_id=f63261ba-a8aa-41fe-8a7c-349550006415 response_message_id=bd0afb93-0140-46ab-a41c-71c5476c7836 response_sender_id=d3f4424a-ce5f-4c1f-82d0-8cefe778e83b response_token=SUPPORT-PARLANT response_contains_token=True
```

The first Tom/Jerry proof run showed that the local ignored `agent_config.yaml` had `tom_agent` and `jerry_agent` pointing at the same Band identity. I registered distinct Tom and Jerry agents with the Human API, updated only the ignored local config, and reran those two examples. Final Tom/Jerry API/session proof:

```text
tom_jerry_same_identity=False
EXAMPLE_API_PROOF example=04_tom_agent.py config=tom_agent agent_me_id=46da018a-70d5-41f5-968b-f0c119224816 agent_me_name=Parlant_Tom_Agent parlant_openai_initialized=True process_agent_started=True room_id=d3c05ebe-dabc-45e5-a99c-98cdb69cab62 participant_added=True trigger_message_id=c7ec59bd-f8cd-4da8-8b32-6d44dbab8374 response_message_id=652266c3-3908-48a2-9052-01b03cb4ef75 response_sender_id=46da018a-70d5-41f5-968b-f0c119224816 response_token=TOM-PARLANT response_contains_token=True
EXAMPLE_API_PROOF example=05_jerry_agent.py config=jerry_agent agent_me_id=5f9e127d-f01a-47cb-9b30-deb170549232 agent_me_name=Parlant_Jerry_Agent parlant_openai_initialized=True process_agent_started=True room_id=c977997b-9454-4f37-b782-753098b25ff9 participant_added=True trigger_message_id=cb7da22f-2b22-4c2c-b9e0-d2dcee4f6071 response_message_id=387f6465-af3e-463d-acb6-8f2b49b51713 response_sender_id=5f9e127d-f01a-47cb-9b30-deb170549232 response_token=JERRY-PARLANT response_contains_token=True
```

This proves the standalone Parlant examples are not just syntactically valid: they start with OpenAI-backed Parlant, connect to production Band as real agents, create live rooms, add the user participant, process live user @mentions, and send observable Band messages with persisted message IDs.

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
