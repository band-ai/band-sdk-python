# Baseline E2E Toolkit

Reusable building blocks for live end-to-end tests that drive real agents against
a real Band platform.

**Coding agents: read "Writing a test" and "Rules" first, then reuse the fixtures
and helpers here — do not rebuild provisioning, waiting, adapter construction, or
assertions.** A baseline test should contain the *scenario*, never the plumbing.
These tools validate platform behaviour and integration, not LLM output quality;
they are deterministic by design (no `sleep`, no silence windows).

Run: `E2E_TESTS_ENABLED=true uv run pytest tests/e2e/baseline/ -v -s --no-cov`

**Wiring a new framework adapter into the matrix?** See
[`ADDING_AN_ADAPTER.md`](ADDING_AN_ADAPTER.md) — the step-by-step howto for
registering an adapter so every matrix scenario runs against it for free.

## Writing a test

Every baseline test is the same shape: get a running agent, open a capture, send a
user message, barrier on it, assert. The toolkit supplies all of it.

```python
@with_agents(Adapter.ANTHROPIC)                  # 1. agent: built + gated + run + reaped
@pytest.mark.asyncio(loop_scope="session")
async def test_greets(agent, resource_manager, user_ops, reply_capture):
    room_id = await resource_manager.provision_room(participants=[agent.id])   # 2. room
    async with reply_capture(room_id) as capture:                             # 3. capture
        mid = await user_ops.send_message(                                    # 4. drive as user
            room_id, "say hi", mention_id=agent.id, mention_name=agent.name
        )
        await capture.wait_for_processed(mid, agent.id)                       # 5. barrier
    assert_present(capture.messages)                                          # 6. assert
```

`@with_agents` / `@across_adapters` auto-apply the `@requires` provider-key gate
from the registry, and the agent/room are reaped on teardown — so the body has no
gate, no construction, no lifecycle, no cleanup.

### Choosing how to get your agent(s)

| Your test needs… | Use | Inject |
|---|---|---|
| one named adapter | `@with_agents(Adapter.ANTHROPIC)` | `agent` — a `ProvisionedAgent` |
| several named adapters in one room | `@with_agents(Adapter.LANGGRAPH, Adapter.ANTHROPIC)` | `agents` — list; `a, b = agents` |
| two of the **same** adapter | `@with_agents(Adapter.ANTHROPIC, Adapter.ANTHROPIC)` | `agents` |
| the **same scenario across every adapter** | request the matrix fixtures (no decorator needed for the full set) | `matrix_agent` + `adapter_id` |
| a **subset** of adapters | `@across_adapters(include={...} / exclude={...} / supports={Capability.MEMORY} / without={Capability.MEMORY})` | `matrix_agent` + `adapter_id` |
| custom tools (any tool-capable framework) | `@with_agents(Adapter.X, tools=[LOOKUP_TOOL], **EXECUTION_REPORTING)` — one `ToolSpec`, translated per framework (anthropic-family, pydantic-ai, agno) | `agent` |
| custom tools across the matrix | `@across_adapters(include={Adapter.ANTHROPIC, Adapter.PYDANTIC_AI, Adapter.AGNO}, tools=[LOOKUP_TOOL], **EXECUTION_REPORTING)` | `matrix_agent` + `adapter_id` |
| a **bespoke** build (different tools per agent in one room) | `build_tool_agent(...)` + `async with running_provisioned_agent(...) as agent:` | the `ProvisionedAgent` |

- **Reference adapters by the typed `Adapter` enum, never a string.**
- **Steer construction with a shape, don't re-spell args:** `@with_agents(Adapter.X, **TOOL_AGENT)`
  (exact-tool-execution prompt) or `**MEMORY_AGENT` (that prompt + memory tools
  surfaced as `tool_call` events). `@across_adapters(..., **MEMORY_AGENT)` works too.
- `matrix_agent` is the cell's running `ProvisionedAgent`; `adapter_id` is its id —
  request whichever you use (no `adapter_id, agent = …` unpacking).
- **Custom tools:** define a tool once as a `ToolSpec` (input model + handler) and
  pass `tools=[LOOKUP_TOOL]` to `@with_agents` / `@across_adapters`; the builders
  translate it to each framework's native form (band `CustomToolDef`, a pydantic-ai
  `RunContext` callable, an agno tool). Add `**EXECUTION_REPORTING` to observe the
  calls via `capture.tool_calls`. Only letta can't accept a local tool (MCP) and
  **rejects** `tools` with a clear error rather than silently dropping it.

### Driving and observing a turn

- **Send as the user:** `mid = await user_ops.send_message(room_id, text, mention_id=, mention_name=)`.
- **Barrier:** `await capture.wait_for_processed(mid, agent.id)` — the only correct
  "agent is done" signal (delivery state, not reply text). Once it returns, the
  reply is already in `capture.messages`.
- **Reused capture, later turn:** `mark = capture.messages.snapshot()` before sending,
  `capture.messages.since(mark)` after the barrier.
- **Inspect (read-after-barrier):** `capture.tool_calls()`, `capture.thoughts()/errors()/tasks()`,
  `capture.memory(agent)` — see the inspection sections below.

## Rules (clean, lean, reuse — no reinvention)

**Do**
- Reuse the fixtures for everything — provisioning, the user driver, capture, waits,
  assertions. Your test is the scenario, not the scaffolding.
- Wait event-driven: `wait_for_processed` / `wait_for_delivery` / `wait_until`.
- Assert cheaply and tolerantly: `assertions.py` / the `Replies` methods first; the
  `judge` only for genuinely semantic outcomes.
- Use the `Adapter` enum, the `**TOOL_AGENT` / `**MEMORY_AGENT` shapes, and
  `capture.messages.snapshot()` / `.since()`.
- Add a new adapter to the matrix with one `@adapter` builder + one `Adapter` member
  (the discovery guard fails loudly until both exist).

**Don't**
- ❌ `time.sleep` / `asyncio.sleep` / fixed silence windows — flaky; use the waiters.
- ❌ hand-rolled provisioning/reaping, raw REST/WS clients, or hand-built adapters
  where a fixture or registry builder exists.
- ❌ `len(capture.messages)` + slice — use `snapshot()` / `since()`.
- ❌ magic-string adapter ids — use `Adapter.X`.
- ❌ a separate `@requires` when `@with_agents` / `@across_adapters` already gate it.
- ❌ exact-count / strict-ordering / mandatory-silence / literal-transcript
  assertions — agents are non-deterministic; assert *behaviour held* (a floor, a
  substring, a metadata fact, the injected marker).
- ❌ the `judge` by reflex — it costs tokens and is itself non-deterministic; use it
  only when no structural check can express the outcome.
- ❌ skipping on missing config — a missing key/CLI/server **fails** with the reason
  (see Validation policy). Only `E2E_TESTS_ENABLED` skips.

## Design values: consistency, simplicity, ease

These three are why the toolkit is shaped the way it is — keep them when extending it.

- **Consistency** — one way to do each thing, applied uniformly. Agents come from
  `@with_agents` / `@across_adapters`; waits go through the delivery barrier;
  assertions are tolerant. The same rule holds everywhere: **fail loudly with a
  reason, never silently** — a missing key/CLI/server fails (never skips), an
  unregistered adapter fails the discovery guard, and an adapter that can't honor
  `tools` rejects rather than dropping them. No special cases a reader has to memorize.
- **Single source of truth** — each fact lives in exactly one place and is referenced,
  never re-spelled: adapter ids are the typed `Adapter` enum (no magic strings —
  `include`/`exclude` take `Adapter` members), a custom tool is one `ToolSpec`, and
  prompt/feature bundles are shapes (`**TOOL_AGENT` / `**MEMORY_AGENT` /
  `**EXECUTION_REPORTING`). Change the fact once; every test follows.
- **Simplicity** — the test is the *scenario*, not the scaffolding. Provisioning,
  gating, running, reaping, and cleanup live in fixtures/decorators, so a test body
  is just "send this, expect that". If a test grows plumbing, the plumbing belongs in
  the toolkit.
- **Ease of use** — the common path is the short path: a decorator + a fixture, typed
  `Adapter` handles (no magic strings), reusable shapes (`**TOOL_AGENT` /
  `**MEMORY_AGENT` / `**EXECUTION_REPORTING`) instead of re-spelled args, and
  `messages.snapshot()/since()` instead of manual indexing. A coding agent should be
  able to write a correct test from the table above without inventing anything.

## Layout: what is where

| Path | What it is |
|------|------------|
| `toolkit/provisioning.py` | `ResourceManager` (provision/reap agents + rooms, orphan sweep), `running_provisioned_agent` (yields the running agent's `ProvisionedAgent`), `ProvisionedAgent` |
| `toolkit/adapters.py` | adapter registry: `Adapter` enum (the **one** source of adapter ids), `@adapter` builders, `build_adapter`, `specs`, the discovery guard |
| `toolkit/tools.py` | `ToolSpec` — define a custom tool **once** (input model + handler); the builders translate it to each framework's native form |
| `agents.py` | matrix/decorator glue: `@with_agents(Adapter.X, ...)` (fixed set → `agent`/`agents`), `@across_adapters(include/exclude/supports/without, prompt=, features=)` (matrix/subset → `matrix_agent` + `adapter_id`), `adapter_params` |
| `smoke/sample_agents.py` | shared driving glue: the role-setting `TOOL_AGENT_SYSTEM_PROMPT`, `memory_features()`, reusable **agent shapes** (`TOOL_AGENT`, `MEMORY_AGENT`) for `@with_agents(..., **SHAPE)`, `build_agent`, and the `*_instruction(...)` builders |
| `smoke/sample_tools.py` | sample custom tools as `ToolSpec`s (`LOOKUP_TOOL`, `WEATHER_TOOL`), prompts, the `EXECUTION_REPORTING` shape, and `build_tool_agent(...)` (bespoke per-agent-differing builds) |
| `toolkit/user_ops.py` | `UserOps`: act as the test user (send message, create/delete room, add/remove/list participants, list messages/events) |
| `toolkit/capture.py` | `ReplyCapture` (subscribe-before-send), `reply_capture` ctx, `wait_for_processed` (delivery-status barrier), `tool_calls()`/`thoughts()`/`errors()`/`tasks()`/`events()`/`memory(agent)`, `CaptureFactory` |
| `toolkit/judge.py` | `judge()` LLM-as-judge, `Verdict`, `format_transcript` |
| `toolkit/assertions.py` | tolerant assertions: `assert_present`, `assert_at_least`, `assert_contains_any`, `assert_mentions` |
| `toolkit/observations/` | list subclasses that own their assertions: `Replies` (replies; `snapshot()`/`since()`), `ToolCalls`/`ToolCall` + `MemoryToolCalls`, `Events`→`Thoughts`/`Errors`/`Tasks`, `Memories`/`MemoryObservation`; shared `tolerant_match` + `ContentAssertions` |
| `settings.py` | `BaselineSettings`: endpoints, credentials, run policy, LLM creds + models |
| `requires.py` | `@requires(Dep.X)` decorator + `Dep` enum |
| `conftest.py` | fixtures (below) + the always-on E2E gate |
| `smoke/` | proof tests that exercise the tools end to end — read these as worked examples |

The `toolkit/` modules are pytest-free and reusable anywhere. The package root
(`settings`, `requires`, `agents`, `conftest`) is the pytest wiring.

## Fixtures (from `conftest.py`)

`baseline_settings`, `user_ops`, `resource_manager`, `reply_capture`, `judge`,
`agent`, `agents`, `adapter_id`, `matrix_agent`, `baseline_ws`.

- `reply_capture` and `judge` pre-bind their plumbing (the WS observer; the judge
  model + key), so tests pass only the test-specific arguments.
- `agent` / `agents` are driven by `@with_agents(Adapter.X, ...)`: the decorator
  auto-applies the requirement gate and the fixtures build (via the registry) +
  provision + run + reap the agents.
- `adapter_id` (the cell's id) + `matrix_agent` (its running `ProvisionedAgent`) are
  parametrized over the full registry by default and narrowed by `@across_adapters`.
- The E2E + Band-key gate is applied to every baseline test automatically, so a
  gate-only test needs no decorator.

## "I want to..." -> use this (do not reinvent)

| Need | Use |
|------|-----|
| Run a specific named agent | `@with_agents(Adapter.ANTHROPIC)` → `agent` (or `agents` for several); auto-gates + runs + reaps |
| Run a standard prompt/features shape | `@with_agents(Adapter.X, **TOOL_AGENT)` / `**MEMORY_AGENT` — don't re-spell `prompt=`/`features=` |
| Run the same scenario across every adapter | request `matrix_agent` and/or `adapter_id` (parametrized over the full registry) |
| Run a scenario across a subset | `@across_adapters(include=/exclude= by id, or supports=/without={Capability.MEMORY} by capability)`; add `prompt=`/`features=` (or `**MEMORY_AGENT`) to steer |
| A bespoke adapter (custom tools) | `build_tool_agent(...)` + `async with running_provisioned_agent(adapter, resource_manager) as agent:` |
| Clean up what I created | nothing: `resource_manager` reaps on teardown (`BAND_E2E_AUTOCLEAN=false` keeps it for debugging) |
| Drive the platform as a user | the `user_ops` fixture (`UserOps`) |
| Observe replies without a race | `async with reply_capture(room_id) as capture:` then send |
| Know a turn/burst finished (reply captured) | `mid = await user_ops.send_message(...)` then `await capture.wait_for_processed(mid, agent_id)` |
| Wait for a specific delivery state (e.g. a failure) | `await capture.wait_for_delivery(mid, agent_id, until={DeliveryStatus.FAILED})` |
| Inspect the delivery lifecycle | `capture.delivery_status(mid, agent_id)` / `capture.delivery_history(mid, agent_id)` |
| Wait on a custom condition | `await capture.wait_until(predicate)` |
| Scope a read to a later turn (reused capture) | `mark = capture.messages.snapshot()` before sending; `capture.messages.since(mark)` after the barrier |
| See which tools fired (with args) | `calls = await capture.tool_calls(sender_id=agent.id)` after the barrier (needs `Emit.EXECUTION`; memory tools excluded — `include_memory=True` or `capture.memory(agent)`) |
| Assert a specific tool fired | `calls.assert_fired("name", with_args={...})` (case-insensitive, subset args) |
| See which events an agent emitted | `await capture.thoughts(sender_id=agent.id)` (or `errors()`/`tasks()`/`events(MessageType.X)`) |
| Assert an event was emitted | `thoughts.assert_emitted()` / `thoughts.assert_contains_any([marker])` |
| Observe an agent's memory (both layers) | `mem = await capture.memory(agent, content_query=marker)` after the barrier |
| Assert a memory op was called / a record landed | `mem.calls.assert_store_called(...)` / `mem.stored.assert_stored(content=marker, ...)` |
| Assert something happened (cheap) | `assertions.py` helpers, or the `Replies` methods on `capture.messages` |
| Assert a fuzzy/semantic outcome | the `judge` fixture (sparingly — see Assertion strategy) |
| Declare extra requirements explicitly | `@requires(Dep.OPENAI, ...)` (missing one **fails**) — but `@with_agents`/`@across_adapters` already do this for the agents they build |

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
- `deadline_s` is a failure deadline only (raises `TimeoutError`); never a success signal.
- `wait_for_processed(message_id, agent_id)` is the way to know an agent is done.
  It reads the platform's `message_updated` delivery state — the same signal the
  runtime itself uses — so it never depends on the agent's reply text. Per-room
  FIFO processing means barriering on the last message you sent proves every earlier
  message was handled; and since `processed` is reported only after the reply is
  emitted, that reply is already in `capture.messages` once it returns. (No probe
  message is needed — `send_message` returns the id to barrier on.)

## Waiting on delivery state (`DeliveryStatus`)

Each message carries a per-recipient delivery state, exposed as
`band.client.streaming.DeliveryStatus`. The backend lifecycle is:

```
DELIVERED -> PROCESSING -> PROCESSED | FAILED
```

`FAILED` is **not** terminal — the platform retries (bounded by max retries), so a
message may cycle `FAILED -> PROCESSING` again before reaching `PROCESSED`.
`PROCESSED` is the only success terminal.

```python
mid = await user_ops.send_message(room_id, "...", mention_id=a.id, mention_name=a.name)

# Success barrier (the common case): wait until PROCESSED. Waits through any
# transient FAILED; on timeout it reports the last status + attempt error.
await capture.wait_for_processed(mid, a.id)

# Any specific state(s): the general waiter, returns the DeliveryStatus reached.
reached = await capture.wait_for_delivery(mid, a.id, until={DeliveryStatus.FAILED})

# Inspect after the fact (no waiting):
capture.delivery_status(mid, a.id)        # current state, or None if unseen
capture.delivery_history(mid, a.id)       # e.g. [PROCESSING, PROCESSED]
```

Note: `DELIVERED` is set at rest but is not pushed as its own WebSocket frame — in
practice the first observed transition is `PROCESSING`. Do not wait on `DELIVERED`.

## Tool-observation inspection (`tool_calls` + `assert_fired`)

After a turn settles (barrier on the trigger id with `wait_for_processed`), read the
agent's tool calls and assert what fired:

```python
mid = await user_ops.send_message(room_id, "...", mention_id=a.id, mention_name=a.name)
await capture.wait_for_processed(mid, a.id)
calls = await capture.tool_calls(sender_id=a.id)   # a ToolCalls (list[ToolCall])
calls.assert_fired("get_weather", with_args={"place": "Zorath"})
```

This reads the persisted `tool_call` events (so the agent must run with
`Emit.EXECUTION` — use `**TOOL_AGENT`-style features), not a live subscription. It is
race-free: the platform marks the trigger `processed` only after the reply is
emitted, by which point the turn's tool-call events are already persisted.
`assert_fired` is tolerant — name matches case-insensitively and `with_args` is a
subset/substring match. Pass `sender_id` to scope to one agent and `since` (a server
timestamp) to scope to one turn when reusing a capture. See `smoke/test_tool_calls.py`
and `smoke/test_isolation.py`.

By default `tool_calls()` **excludes memory tools** (mirroring the SDK's
`BASE_TOOL_NAMES = ALL_TOOL_NAMES - MEMORY_TOOL_NAMES` split). Pass
`include_memory=True`, or use `capture.memory(agent)` for the dedicated memory view.

## Emitted-event inspection

`capture.thoughts()` / `errors()` / `tasks()` (or generic `capture.events(MessageType.X)`)
return an `Events` collection on the same read-after-barrier contract as `tool_calls`.
Drive them with the built-in `band_send_event` tool (no `Emit.*` feature needed; the
tool posts directly). `assert_emitted()` and `assert_contains_any([marker])` are the
assertions; assert the **marker** (not bare presence), since adapters auto-emit a
generic `error` event on any turn exception. See `smoke/test_events.py`.

## Memory inspection

`capture.memory(agent)` reads both observable layers in one call, returning a
`MemoryObservation`:

- **Call layer** — `mem.calls` (a `MemoryToolCalls`), from the room's `tool_call`
  events: `mem.calls.assert_store_called(scope=..., system=..., type=...)`,
  `assert_list_called()`, etc. Needs `Emit.EXECUTION` (use `**MEMORY_AGENT`).
- **Store layer** — `mem.stored` (a `Memories`) of records that *actually landed*,
  from the memories API: `mem.stored.where(scope=..., system=...)` +
  `.assert_stored(...)` / `.assert_present()` / `.assert_none()`.

`memory()` takes the agent handle because the store layer needs the agent's own key.
Drive a store with `band_store_memory`, read after the barrier; a unique marker keeps
the read collision-free. Memory tools are an enterprise opt-in (entitled org). See
`smoke/test_memory.py`.

## Validation policy: fail on missing requirements, never skip

A test that needs a key/CLI/server and can't find it **fails** — it does not skip.
Skipping on missing config hides misconfiguration as a false green. The only
legitimate skip is `E2E_TESTS_ENABLED` (the on/off switch for the whole live suite);
`BAND_API_KEY_USER` missing while E2E is enabled **fails** (the always-on gate), and
any `@requires(Dep.X)` requirement **fails** when absent, naming the missing env
var/CLI/server. Consequence: no single environment turns the full adapter matrix
green (crewai needs the `dev-crewai` lane; codex/opencode/letta need their backend) —
a red cell means "this backend isn't wired up", which is intended.

## Dependency lanes (the crewai mutual exclusion)

`crewai` **cannot be installed in the same environment** as `pydantic-ai` or
`parlant` — their transitive pins conflict (pydantic `<2.12` vs `>=2.12`,
opentelemetry `<1.35` vs `>=1.37`). `pyproject.toml` declares this under
`[tool.uv] conflicts`, so `uv` resolves two separate environments ("lanes"):

| Lane | Install | Has | Lacks |
|------|---------|-----|-------|
| **default** | `uv sync --extra dev` | everything incl. `pydantic_ai` | `crewai`, `crewai_flow` |
| **crewai** | `uv sync --extra dev-crewai` | `crewai`, `crewai_flow` | `pydantic_ai`, `parlant` |

**Run / switch locally** — re-sync to flip lanes (same command, different extra):

```bash
uv sync --extra dev            # default lane
E2E_TESTS_ENABLED=true uv run pytest tests/e2e/baseline/ -v -s --no-cov

uv sync --extra dev-crewai     # crewai lane (overwrites the env)
E2E_TESTS_ENABLED=true uv run pytest tests/e2e/baseline/ -k crewai -v -s --no-cov
```

**How the matrix handles it** — the full registry stays the source of truth (the
discovery guard still requires all 12 adapters registered, lane-independent), but
no single lane can run all of them. Coverage is the **union of both lanes**: CI runs
the suite twice (`--extra dev` and `--extra dev-crewai`), and an adapter absent from
the current lane is covered by the other. Within a lane, a cross-lane adapter's
matrix cell is reported as a failure-with-reason (its framework isn't importable
here) rather than hidden — so the gap is visible, and the other lane is where it
goes green. To keep a single local run clean, scope it: `-k crewai` in the crewai
lane, or `@across_adapters(exclude={Adapter.CREWAI, Adapter.CREWAI_FLOW})` /
`include={...}` to target the lane you're in.
