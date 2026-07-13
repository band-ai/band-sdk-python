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
| `toolkit/user_ops.py` | `UserOps`: act as the test user (send message, `send_structured_mentions` / `send_mentioning_all`, create/delete room, add/remove/list participants, list messages/events) |
| `toolkit/capture.py` | `ReplyCapture` (subscribe-before-send), `reply_capture` ctx, `wait_for_processed` (delivery-status barrier), `tool_calls()`/`thoughts()`/`errors()`/`tasks()`/`events()`/`memory(agent)` |
| `toolkit/waits.py` | reply-presence waits without PROCESSED delivery status: `wait_for_reply_from`, `wait_for_replies_from`, `said_by` |
| `toolkit/exchange.py` | run-scoped `marker()`, bounded `run_exchange`, ordered `MentionChainStep` / `MentionChainOutcome` |
| `toolkit/visibility.py` | history visibility outcome types (`Seed`, `VisibilityOutcome`) for mention-scoped context probes |
| `toolkit/discovery.py` | `owner_hub_room_ids` — rooms whose participants are exactly owner + agent |
| `toolkit/judge.py` | `judge()` LLM-as-judge, `Verdict`, `format_transcript` |
| `toolkit/assertions.py` | tolerant assertions: `assert_present`, `assert_at_least`, `assert_contains_any`, `assert_mentions` |
| `toolkit/observations/` | list subclasses that own their assertions: `Replies` (replies), `ToolCalls`/`ToolCall` + `MemoryToolCalls` (tool calls; memory excluded by default), `Events`→`Thoughts`/`Errors`/`Tasks` (emitted events), `Memories`/`MemoryObservation` (stored memory, both layers); shared `tolerant_match` + `ContentAssertions` |
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
| See which tools an agent fired (with args) | `calls = await capture.tool_calls(sender_id=agent.id)` after the barrier (agent needs `Emit.EXECUTION`; memory tools excluded — pass `include_memory=True` or use `capture.memory(agent)`) |
| Assert a specific tool fired | `calls.assert_fired("name", with_args={...})` (case-insensitive name, subset args) |
| See which events an agent emitted | `await capture.thoughts(sender_id=agent.id)` (or `errors()`/`tasks()`/`events(MessageType.X)`; read after the barrier) |
| Assert an event was emitted | `thoughts.assert_emitted()` / `thoughts.assert_contains_any([marker])` |
| Observe an agent's memory (both layers) | `mem = await capture.memory(agent, content_query=marker)` after the barrier |
| Assert a memory operation was called | `mem.calls.assert_store_called(scope=..., system=...)` |
| Assert a memory actually landed in the store | `mem.stored.assert_stored(content=marker, system=...)` |
| Assert something happened (cheap) | `assertions.py` helpers, or the methods on `capture.messages` (`Replies`) |
| Assert a fuzzy/semantic outcome | the `judge` fixture (use sparingly, see below) |
| Build a cheap agent to run | the `langgraph_adapter` / `anthropic_adapter` fixtures |
| Declare a test's extra requirements | `@requires(Dep.OPENAI, ...)` (a missing one **fails**, see Validation policy; the E2E + Band-key gate is automatic) |

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

## Tool-observation inspection (`tool_calls` + `assert_fired`)

After a turn settles (barrier on the trigger id with `wait_for_processed`), read
the agent's tool calls and assert what fired:

```python
mid = await user_ops.send_message(room_id, "...", mention_id=a.id, mention_name=a.name)
await capture.wait_for_processed(mid, a.id)
calls = await capture.tool_calls(sender_id=a.id)   # a ToolCalls (list[ToolCall])
calls.assert_fired("get_weather", with_args={"place": "Zorath"})
```

This reads the persisted `tool_call` events (so the agent must run with
`Emit.EXECUTION`), not a live subscription. It is race-free because the platform
marks the trigger `processed` only after the reply is emitted, by which point the
turn's tool-call events are already persisted. `assert_fired` is tolerant: the
name matches case-insensitively and `with_args` is a subset/substring match, not
exact args. Pass `sender_id` to scope to one agent, and `since` (a server
timestamp) to scope to one turn when reusing a capture. See
`smoke/test_tool_calls.py` and `smoke/test_isolation.py`.

By default `tool_calls()` **excludes memory tools** (mirroring the SDK's own
`BASE_TOOL_NAMES = ALL_TOOL_NAMES - MEMORY_TOOL_NAMES` split) so generic tool
assertions aren't polluted by memory operations. Pass `include_memory=True` to
keep them, or use `capture.memory(agent)` for the dedicated memory view (below).

## Emitted-event inspection

`capture.thoughts()` / `errors()` / `tasks()` (or the generic
`capture.events(MessageType.X)`) return an `Events` collection of the free-text
events an agent emitted, on the same read-after-barrier contract as `tool_calls`.
Drive them with the built-in `band_send_event` tool — the direct LLM-facing way
to create a `thought`/`error`/`task` message (no `Emit.*` feature needed; the
tool posts directly). Run such agents **without** `Emit.EXECUTION` when you want
the room history to contain only the events you drove, not tool-call telemetry.
`assert_emitted()` and `assert_contains_any([marker])` are the assertions; assert
the **marker** (not bare presence), since adapters auto-emit a generic `error`
event on any turn exception. See `smoke/test_events.py`.

## Memory inspection

Memory has two observable layers, and `capture.memory(agent)` reads both in one
call, returning a `MemoryObservation`:

- **Call layer** — `mem.calls` is a `MemoryToolCalls` (a `ToolCalls` restricted to
  the memory tools), read from the room's `tool_call` events via the observer
  client. Operation-named assertions read clearer than raw `assert_fired`:
  `mem.calls.assert_store_called(scope=..., system=..., type=...)`,
  `assert_list_called()`, etc. Needs `Emit.EXECUTION`.
- **Store layer** — `mem.stored` is a `Memories` of records that *actually
  landed*, read from the memories API. Filter with
  `mem.stored.where(scope=..., system=...)` and assert with `.assert_stored(...)`
  / `.assert_present()` / `.assert_none()`.

`memory()` takes the agent handle because the store layer needs the agent's own
key (the observer client can't see it). The names keep the altitudes distinct:
`assert_store_called` (invoked) vs `assert_stored` (a record exists). Drive a
store with `band_store_memory`, read after the barrier; a unique marker keeps the
read collision-free. Memory tools are an enterprise opt-in, so the store layer
needs an entitled org. See `smoke/test_memory.py`.

## Validation policy: fail on missing requirements, never skip

A test that needs a key or resource and can't find it **fails** — it does not
skip. Skipping on missing config hides misconfiguration as a false green. The
only legitimate skip is `E2E_TESTS_ENABLED` (the on/off switch for the whole live
suite); `BAND_API_KEY_USER` missing while E2E is enabled **fails** (the always-on
gate), and any further `@requires(Dep.X)` requirement **fails** when absent, with
the env-var name as the reason.

## Not here yet

- A full LLM-judge harness (calibration, voting/pass^k, tool-correctness) is
  later work; `judge.py` notes DeepEval as the likely path.
