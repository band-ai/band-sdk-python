# Baseline E2E Toolkit

Reusable building blocks for live end-to-end tests that drive real agents
against a real Band platform. Read this before writing a new baseline test or
adding a helper, so you reuse what exists instead of rebuilding it.

These tools validate platform behaviour and integration, not LLM output
quality. They are deterministic by design (no `sleep`, no silence windows).

## Layout: what is where

| Path | What it is |
|------|------------|
| `toolkit/provisioning.py` | `ResourceManager` (provision/reap agents + rooms, orphan sweep), `running_provisioned_agent`, `ProvisionedAgent` |
| `toolkit/user_ops.py` | `UserOps`: act as the test user (send message, create/delete room, add/remove/list participants) |
| `toolkit/waiting.py` | `ReplyCapture` (subscribe-before-send), `reply_capture` ctx, `wait_for_processed` (delivery-status barrier) |
| `toolkit/judge.py` | `judge()` LLM-as-judge, `Verdict`, `format_transcript` |
| `toolkit/assertions.py` | tolerant assertions: `assert_present`, `assert_at_least`, `assert_contains_any`, `assert_mentions` |
| `settings.py` | `BaselineSettings`: endpoints, credentials, run policy, LLM creds + models |
| `requires.py` | `@requires(Dep.X)` decorator + `Dep` enum |
| `conftest.py` | fixtures (below) + the always-on E2E gate |
| `smoke/` | proof tests that exercise the tools end to end |

The `toolkit/` modules are pytest-free and reusable anywhere. The package root
(`settings`, `requires`, `conftest`) is the pytest wiring.

## "I want to..." -> use this (do not reinvent)

| Need | Use |
|------|-----|
| A fresh agent + room for this test | `resource_manager.provision_agent(label)` / `provision_room(...)`, or `running_provisioned_agent(adapter, resource_manager)` to also run it |
| Clean up what I created | nothing: `resource_manager` reaps on teardown (set `BAND_E2E_AUTOCLEAN=false` to keep for debugging) |
| Drive the platform as a user | the `user_ops` fixture (`UserOps`) |
| Observe agent replies without a race | `async with reply_capture(room_id) as capture:` then send |
| Know the agent finished a turn / burst (and capture its reply) | `mid = await user_ops.send_message(...)` then `await capture.wait_for_processed(mid, agent_id)` |
| Wait for a specific delivery state (e.g. observe a failure) | `await capture.wait_for_delivery(mid, agent_id, until={DeliveryStatus.FAILED})` |
| Inspect the delivery lifecycle that occurred | `capture.delivery_status(mid, agent_id)` / `capture.delivery_history(mid, agent_id)` |
| Wait on a custom condition | `await capture.wait_until(predicate)` |
| Assert something happened (cheap) | `assertions.py` helpers |
| Assert a fuzzy/semantic outcome | the `judge` fixture (use sparingly, see below) |
| Build a cheap agent to run | the `langgraph_adapter` / `anthropic_adapter` fixtures |
| Gate a test on an optional dependency | `@requires(Dep.OPENAI, ...)` (the E2E + Band-key gate is automatic) |

## Fixtures (from `conftest.py`)

`baseline_settings`, `user_ops`, `resource_manager`, `reply_capture`,
`judge`, `langgraph_adapter`, `anthropic_adapter`, `baseline_ws`. The
`reply_capture` and `judge` fixtures pre-bind their plumbing (the WS observer;
the judge model + key), so tests pass only the test-specific arguments. The
E2E + Band-key gate is applied to every baseline test automatically, so a
gate-only test needs no decorator.

## Assertion strategy: cheap checks first, judge last

Prefer the cheapest assertion that proves the point. The LLM judge costs tokens,
adds latency, and is itself non-deterministic, so do not reach for it by reflex.

1. Structural facts -> `assertions.py` (`assert_present`, `assert_at_least`,
   `assert_contains_any`, `assert_mentions`). Free, instant, deterministic.
2. Only when the outcome is genuinely semantic (paraphrase-proof "did it greet?",
   "did it recall both facts?") -> the `judge` fixture.

Use the structural assertions as a fast pre-check before the judge, as the smoke
tests do. If a substring or metadata check can express the assertion, use it
instead of the judge.

## Conventions

- Waits are event-driven and deterministic. Never `sleep` or poll a fixed window.
- `deadline_s` is a failure deadline only (raises `TimeoutError`); it is never a
  success signal.
- `wait_for_processed(message_id, agent_id)` is the way to know an agent is done.
  It reads the platform's `message_updated` delivery state — the same signal the
  runtime itself uses — so it never depends on the agent's reply text. Per-room
  FIFO processing means barriering on the last message you sent proves every
  earlier message was handled; and since `processed` is reported only after the
  reply is emitted, that reply is already in `capture.messages` once it returns.
  (No probe message is needed — `send_message` returns the id to barrier on.)

## Waiting on delivery state (`DeliveryStatus`)

Each message carries a per-recipient delivery state, exposed as
`band.client.streaming.DeliveryStatus`. The backend lifecycle is:

```
DELIVERED -> PROCESSING -> PROCESSED | FAILED
```

`FAILED` is **not** terminal — the platform retries (bounded by max retries),
so a message may cycle `FAILED -> PROCESSING` again before reaching `PROCESSED`.
`PROCESSED` is the only success terminal.

Pick the waiter for what you need:

```python
mid = await user_ops.send_message(room_id, "...", mention_id=a.id, mention_name=a.name)

# Success barrier (the common case): wait until PROCESSED. Waits through any
# transient FAILED; on timeout it reports the last status + attempt error.
await capture.wait_for_processed(mid, a.id)

# Any specific state(s): the general waiter, returns the DeliveryStatus reached.
reached = await capture.wait_for_delivery(mid, a.id, until={DeliveryStatus.FAILED})
reached = await capture.wait_for_delivery(
    mid, a.id, until={DeliveryStatus.PROCESSED, DeliveryStatus.FAILED}
)

# Inspect after the fact (no waiting):
capture.delivery_status(mid, a.id)        # current state, or None if unseen
capture.delivery_history(mid, a.id)       # e.g. [PROCESSING, PROCESSED]
```

Note: `DELIVERED` is set at rest but is not pushed as its own WebSocket frame —
in practice the first observed transition is `PROCESSING`. Do not wait on
`DELIVERED` over the channel.

## A minimal test

```python
@requires(Dep.ANTHROPIC)
@pytest.mark.asyncio(loop_scope="session")
async def test_example(resource_manager, user_ops, reply_capture, anthropic_adapter):
    async with running_provisioned_agent(anthropic_adapter, resource_manager) as (_, agent):
        room_id = await resource_manager.provision_room(participants=[agent.id])
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(
                room_id, "say hi", mention_id=agent.id, mention_name=agent.name
            )
            await capture.wait_for_processed(mid, agent.id)
        assert_present(capture.messages)
```

Run: `E2E_TESTS_ENABLED=true uv run pytest tests/e2e/baseline/ -v -s --no-cov`

## Not here yet

- Trajectory / tool-observation inspection and the `tool_fired` assertion (assert
  which tool fired, with which args) are a tracked follow-up. Do not build an ad
  hoc version here; extend that work when it lands.
- A full LLM-judge harness (calibration, voting/pass^k, tool-correctness) is
  later work; `judge.py` notes DeepEval as the likely path.
