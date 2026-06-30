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

```python notest
@with_agents(Adapter.ANTHROPIC)                  # 1. agent: built + gated + run + reaped
@pytest.mark.asyncio(loop_scope="session")
async def test_greets(agent, resource_manager, user_ops, reply_capture):
    room_id = await resource_manager.provision_room(participants=[agent.id])   # 2. room
    async with reply_capture(room_id) as capture:                             # 3. capture
        mid = await user_ops.send_message(                                    # 4. drive as user
            room_id, "say hi", mention_id=agent.id, mention_name=agent.name
        )
        await capture.wait_for_processed(mid, agent.id)                       # 5. barrier
    capture.messages.assert_present()                                         # 6. assert
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
- Assert cheaply and tolerantly: the `Replies`/`Events`/`Memories` assertion
  methods first; the `judge` only for genuinely semantic outcomes.
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
| `smoke/samples/sample_agents.py` | shared driving glue: the role-setting `TOOL_AGENT_SYSTEM_PROMPT`, `memory_features()`, reusable **agent shapes** (`TOOL_AGENT`, `MEMORY_AGENT`) for `@with_agents(..., **SHAPE)`, `build_agent`, and the `*_instruction(...)` builders |
| `smoke/samples/sample_tools.py` | sample custom tools as `ToolSpec`s (`LOOKUP_TOOL`, `WEATHER_TOOL`), prompts, the `EXECUTION_REPORTING` shape, and `build_tool_agent(...)` (bespoke per-agent-differing builds) |
| `toolkit/user_ops.py` | `UserOps`: act as the test user (send message, create/delete room, add/remove/list participants, list messages/events) |
| `toolkit/capture.py` | `ReplyCapture` (subscribe-before-send), `reply_capture` ctx, `wait_for_processed` (delivery-status barrier), `tool_calls()`/`thoughts()`/`errors()`/`tasks()`/`events()`/`memory(agent)`, `CaptureFactory` |
| `toolkit/requirements.py` | pytest-free requirement facts: `Dep` enum, `DepSpec` predicates, `Lane`/`LANE_EXTRAS`, `dep_lane` (the **one** source of the `Dep`/lane facts the registry references without importing pytest) |
| `toolkit/judge.py` | `judge()` LLM-as-judge, `Verdict`, `format_transcript` |
| `toolkit/observations/` | list subclasses that own their assertions: `Replies` (replies; `snapshot()`/`since()`), `ToolCalls`/`ToolCall` + `MemoryToolCalls`, `Events`→`Thoughts`/`Errors`/`Tasks`, `Memories`/`MemoryObservation`; shared `tolerant_match` + `ContentAssertions` |
| `settings.py` | `BaselineSettings`: endpoints, credentials, run policy, LLM creds + models |
| `requires.py` | `@requires(Dep.X)` decorator (the pytest glue; re-exports `Dep` from `toolkit/requirements.py`, where the enum and its facts actually live) |
| `conftest.py` | fixtures (below) + the always-on E2E gate |
| `guards/` | harness self-tests (not "smoke"): `test_adapter_registry.py` (the static discovery/lane guard — constructs nothing, needs no keys), `test_provisioning.py`, `test_user_ops.py` |
| `smoke/` | proof tests that exercise the tools end to end — read these as worked examples — grouped by subject (below) |
| `smoke/samples/` | shared driving glue (not tests): `sample_agents.py`, `sample_tools.py` |
| `smoke/matrix/` | runs across the adapter matrix: `test_adapter_matrix.py`, `test_capability_matrix.py` |
| `smoke/behavior/` | platform/transport + scenario behavior: `test_delivery_status.py`, `test_processing_barrier.py`, `test_isolation.py`, `test_agent_scenarios.py` |
| `smoke/inspection/` | `capture.*` observation worked-examples: `test_tool_calls.py`, `test_events.py`, `test_memory.py` |
| `smoke/adapters/` | adapter-specific showcases: `test_letta.py` |

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
| Assert an event was emitted | `thoughts.assert_present()` / `thoughts.assert_contains_any([marker])` |
| Observe an agent's memory (both layers) | `mem = await capture.memory(agent, content_query=marker)` after the barrier |
| Assert a memory op was called / a record landed | `mem.calls.assert_store_called(...)` / `mem.stored.assert_stored(content=marker, ...)` |
| Assert something happened (cheap) | the `Replies` assertion methods on `capture.messages` |
| Assert a fuzzy/semantic outcome | the `judge` fixture (sparingly — see Assertion strategy) |
| Declare extra requirements explicitly | `@requires(Dep.OPENAI, ...)` (missing one **fails**) — but `@with_agents`/`@across_adapters` already do this for the agents they build |

## Assertion strategy: cheap checks first, judge last

Prefer the cheapest assertion that proves the point. The LLM judge costs tokens,
adds latency, and is itself non-deterministic, so do not reach for it by reflex.

1. Structural facts -> the tolerant assertion methods on `capture.messages`
   (`Replies`): `assert_present`, `assert_at_least`, `assert_contains_any`,
   `assert_mentions` (and the matching ones on `Events`/`Memories`). Free,
   instant, deterministic.
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

```python notest
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

```python notest
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
timestamp) to scope to one turn when reusing a capture. See `smoke/inspection/test_tool_calls.py`
and `smoke/behavior/test_isolation.py`.

By default `tool_calls()` **excludes memory tools** (mirroring the SDK's
`BASE_TOOL_NAMES = ALL_TOOL_NAMES - MEMORY_TOOL_NAMES` split). Pass
`include_memory=True`, or use `capture.memory(agent)` for the dedicated memory view.

## Emitted-event inspection

`capture.thoughts()` / `errors()` / `tasks()` (or generic `capture.events(MessageType.X)`)
return an `Events` collection on the same read-after-barrier contract as `tool_calls`.
Drive them with the built-in `band_send_event` tool (no `Emit.*` feature needed; the
tool posts directly). `assert_present()` and `assert_contains_any([marker])` are the
assertions; assert the **marker** (not bare presence), since adapters auto-emit a
generic `error` event on any turn exception. See `smoke/inspection/test_events.py`.

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
`smoke/inspection/test_memory.py`.

## Validation policy: fail on missing requirements, never skip

A test that needs a key/CLI/server and can't find it **fails** — it does not skip.
Skipping on missing config hides misconfiguration as a false green. The only
legitimate skip is `E2E_TESTS_ENABLED` (the on/off switch for the whole live suite);
`BAND_API_KEY_USER` missing while E2E is enabled **fails** (the always-on gate), and
any `@requires(Dep.X)` requirement **fails** when absent, naming the missing env
var/CLI/server. Consequence: no single environment turns the full adapter matrix
green in one job (crewai needs its own venv; codex/opencode/letta need a backend) —
a red cell means "this backend isn't wired up", which is intended. The one
deliberate exception is **lane scoping** (`BAND_E2E_LANE`, see CI lanes below): an
*out-of-lane* adapter *skips with a reason* (it's covered by its own lane, so this
is sharding, not hiding), while an **in-lane** adapter with a missing key/backend
still fails.

## CI lanes (a lane = one CI job)

A **lane** is a CI job: a `uv` extra to install plus, for a lane with a server/CLI,
the setup that stands it up. Each registered adapter belongs to exactly one lane,
**derived from its `requires`** (`dep_lane` in `requirements.py` → the unique
non-default lane among its deps). `ci_lanes()` (`toolkit/adapters.py`) groups every
adapter into a `CILane(id, extra, adapters)` — so a newly-registered adapter joins
its lane for free, and the `assert_every_adapter_has_a_ci_home()` guard fails loudly
if one lands nowhere.

Lane ids are **content-based** (what the lane runs) and **decoupled from the `uv`
extra** a lane installs (`Lane` → `Extra` via `LANE_EXTRAS`): several lanes share
the `dev` extra but are split out for isolation.

| Lane | `uv` extra | Adapters | Backend the CI job provides |
|------|-----------|----------|------------------------------|
| `core` | `dev` | anthropic, claude_sdk, agno, langgraph, pydantic_ai | provider keys (secrets) |
| `crewai` | `dev-crewai` | crewai, crewai_flow | provider keys; isolated venv (crewai conflicts with `dev`'s deps — `pyproject.toml [tool.uv] conflicts`) |
| `google` | `dev` | gemini, google_adk | provider keys; split from `core` so Google free-tier rate-limit flakiness is isolated |
| `backends` | `dev` | codex, opencode | the CLI/server coding agents in one job: the `codex` CLI + login + a disposable `CODEX_CWD` (+ the codex-acp e2e), and a running `opencode serve` (`OPENCODE_BASE_URL`) |
| `letta` | `dev` | letta | a self-hosted `letta/letta` server (Docker, auto-relay — no Band MCP) |

`backends` folds codex + opencode into one job (both install `dev`, differ only in
the backend their job stands up) so a job-per-backend isn't needed; the cost is that
one backend failing to come up can redden the other's cells (the per-adapter report
still shows which). `google` and `letta` get their own lanes because their failure
modes (Google rate limits; the self-hosted Letta server) are best isolated.

**The knob:** `BAND_E2E_LANE=<lane id>`. When set, `lane_selection.apply_lane_skips`
(called by the conftest hook) resolves the lane's adapters from `ci_lanes()` and
marks **skip-with-reason** every test bound to an out-of-lane adapter — matrix cells
*and* `@with_agents` tests. An **in-lane** adapter is left untouched, so a missing
key/CLI/server still **fails** via its `@requires` gate (an unwired lane is red by
design until its setup lands). Adapter-agnostic tests always run. **Unset** (the
local default) runs the full matrix, fail-loud.

**Run locally:**

```bash
uv sync --extra dev            # the core/google/backends/letta venv
BAND_E2E_LANE=core E2E_TESTS_ENABLED=true \
  uv run pytest tests/e2e/baseline/ -v -s --no-cov

uv sync --extra dev-crewai     # the crewai lane (overwrites the env)
BAND_E2E_LANE=crewai E2E_TESTS_ENABLED=true \
  uv run pytest tests/e2e/baseline/ -v -s --no-cov
```

**CI** (`.github/workflows/e2e.yml`) lists no adapters: a `lanes` job emits the
partition from `ci_lanes()` as `[{lane, extra}, …]` and the `e2e` job fans one job
per lane (`uv sync --extra <extra>` + `BAND_E2E_LANE=<lane>`), running each lane's
setup steps gated on its `matrix.lane` id. Manual dispatch also takes a `lane` input
(a dropdown, default `all`) validated against the registry, to run one lane on demand.
Adding an adapter to an existing lane needs no YAML edit. Coverage is the union of
all lanes.

### Adding a CI lane

Lanes live in the registry, not the workflow YAML. To add one:

1. **`toolkit/requirements.py`** — add a `Lane` member (a content-based id) and map
   it to its `uv` extra in `LANE_EXTRAS` (add an `Extra` member first if it's a new
   extra — and declare that extra in `pyproject.toml [project.optional-dependencies]`).
2. **`toolkit/requirements.py`** — point a `Dep` at the lane via `lane=Lane.<NEW>` in
   `_DEPS` (provider-key deps with no isolation need ride `DEFAULT_LANE`). The
   adapters whose `requires` include that dep now resolve into the new lane; the
   guards (`assert_every_adapter_has_a_ci_home`, the partition test) keep it honest.
3. **`.github/workflows/e2e.yml`** — only if the lane needs a server/CLI: add a
   `.github/scripts/setup-<backend>.sh` (export any discovered config via
   `$GITHUB_ENV`; see the codex/opencode/letta scripts) and a step that runs it
   gated on its `matrix.lane` id. Add the lane id to the `lane` dispatch-input
   `options` so it's selectable, and update the header comment's lane list. (The
   common env setup — git/uv/python — is the shared `./.github/actions/setup-e2e`
   composite, so a new lane doesn't repeat it.)
4. **Validate:** `assert_workflow_lane_gates_known()` ties every `matrix.lane ==`
   gate back to the registry, and `assert_workflow_lane_options_match_registry()`
   ties the dispatch `lane` dropdown to it — so a typo'd/stale gate, or a dropdown
   that's missing the new lane (or still lists a removed one), fails loudly in the
   guard suite on every PR rather than silently never running / never being
   selectable.

## Letta runs in auto-relay mode

Letta is **a normal matrix cell** — built by the registry like every other adapter
(`build_adapter`) and run through `matrix_agent`/`agents`, with no Letta-only run
path and no fixture special-case.

The one Letta-specific fact is *how it replies*. Letta is server-side and its model
normally talks only through MCP tools — but a self-hosted Letta server **cannot
reach an in-process Band MCP server**: its SSRF guard rejects any MCP URL on a
private/loopback IP (`Non-public IP not allowed`), and the one local transport it
would accept (stdio) isn't registrable via its REST API. So the lane builds the
adapter in **auto-relay mode** (`mcp_server_url=None`): no MCP server is registered,
and `LettaAdapter` relays the model's plain-text reply to the room itself via its
runtime tools. Setup is therefore trivial — a plain `docker run -p 8283:8283
letta/letta`, no `--network host`, no tunnel.

This validates the **reply** path end to end (live platform + live Letta server +
live model → reply delivered); the MCP tool-execution path is covered by the mocked
adapter unit tests (`tests/adapters/test_letta_adapter.py`). Letta advertises no
capabilities, so it is excluded from the memory and custom-tool matrices. To run a
Letta deployment that *does* expose a publicly-reachable Band MCP endpoint, set
`MCP_SERVER_URL` (the adapter then registers it and uses the tool path).
