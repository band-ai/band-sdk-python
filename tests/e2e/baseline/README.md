# Baseline E2E Toolkit

Reusable building blocks for live end-to-end tests that drive real agents against
a real Band platform.

**Coding agents: read "Writing a test" and "Rules" first, then reuse the fixtures
and helpers here — do not rebuild provisioning, waiting, adapter construction, or
assertions.** A baseline test should contain the *scenario*, never the plumbing.
These tools validate platform behaviour and integration, not LLM output quality;
they are deterministic by design (no `sleep`, no silence windows).

**Search before you build — do not reinvent the wheel.** Before writing a new
test, helper, tool, prompt, or assertion, grep the existing suite for one that
already does it (or nearly does):

- **A scenario like yours may already exist.** `grep -rl` the `smoke/` tree for
  the behaviour; if a test covers it for one adapter or a hardcoded subset,
  *parametrize/extend it* (or flip a registry selector — `runs_tool_loop=True`,
  `supports={Capability.MEMORY}`) instead of adding a parallel test. A matrix test
  supersedes a hardcoded-list one; delete what it subsumes rather than stacking.
- **Custom tools / driving instructions already exist** in
  `smoke/samples/sample_tools.py` (`LOOKUP_TOOL`, `WEATHER_TOOL`, `EXECUTION_REPORTING`)
  and `smoke/samples/sample_agents.py` (`store_memory_instruction`,
  `recall_memory_instruction`, `emit_event_instruction`, the `**TOOL_AGENT` /
  `**MEMORY_AGENT` shapes). Reuse a `ToolSpec`/instruction; add one there (not
  inline, not a duplicate) only if none fits.
- **Assertions live on the observation collections** (`Replies`, `Events`,
  `ToolCalls`, `Memories`) — reach for `assert_contains_any` / `assert_contains_none`
  / `assert_fired` / `assert_stored` before writing a raw `assert`. Add a method
  there only if it's a genuinely new, reusable check.
- **Never hardcode an adapter list.** Use the `Adapter` enum with `@with_adapters`,
  or a registry selector with `@per_adapter` — the matrix follows the registry.

When you do add something reusable, put it in the shared module (a `ToolSpec` in
`sample_tools`, an instruction in `sample_agents`, an assertion on the collection)
so the next agent finds and reuses it instead of writing a third copy.

Run: `E2E_TESTS_ENABLED=true uv run pytest tests/e2e/baseline/ -v -s --no-cov`

**Wiring a new framework adapter into the matrix?** See
[`ADDING_AN_ADAPTER.md`](ADDING_AN_ADAPTER.md) — the step-by-step howto for
registering an adapter so every matrix scenario runs against it for free.

## Writing a test

Every baseline test is the same shape: get a running agent, open a capture, send a
user message, barrier on it, assert. The toolkit supplies all of it.

```python notest
@with_adapters(Adapter.ANTHROPIC)                  # 1. agent: built + gated + run + reaped
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

`@with_adapters` / `@per_adapter` auto-apply the `@requires` provider-key gate
from the registry, and the agent/room are reaped on teardown — so the body has no
gate, no construction, no lifecycle, no cleanup.

Every test declares its topology **on the test**, with one of two decorators:

- **`@per_adapter(...)`** *fans* — one invocation per selected adapter. Bare
  `@per_adapter()` is the full matrix, explicitly. Request `agent` (a managed,
  running `ProvisionedAgent`; its id is `agent.adapter_id`) or `cell` (an
  `AdapterCell` you drive yourself, for construction / reboot / rehydration).
- **`@with_adapters(...)`** *groups* — a fixed set of named adapters in one room, one
  invocation. Request `agent` (the single case) or `agents` (the list).

A matrix test **must** carry `@per_adapter` — there is no "bare fixture = full
matrix" path. Requesting an agent fixture with the wrong (or no) decorator is a
collection-time error (see **Wiring fences** below).

### `@per_adapter` vs `@with_adapters` — the two roles

They are **not** two ways to do one thing; they are different *topologies*. You pass
`Adapter` handles to both (hence both read as `*_adapters`), but the axis differs:

| | `@per_adapter` (fan) | `@with_adapters` (group) |
|---|---|---|
| **Runs** | once **per** selected adapter (parametrized) | **once**, all adapters together |
| **Agents per run** | one (`agent`), or a `cell` you drive | many, in one room (`agents`) |
| **Select by** | *filters* — `supports=`, `exclude=`, "every memory adapter" | *explicit ids* — you name the room's participants |
| **CI lanes** | lane-safe (each cell runs in its home lane); a `peer=` folds a second framework in | can span lanes → the schedulability guard catches an unschedulable span |
| **Use for** | "run this scenario across frameworks" | "these specific agents interact" |

You cannot express a multi-agent-in-one-room test with `@per_adapter` (it fans them into
separate runs), nor "across the whole matrix" with `@with_adapters` (a fixed list, one
room). What they *share* — `@requires` gating, `AdapterCell` construction, and
`prompt`/`features`/`tools` steering — is reused, not duplicated.

### Choosing how to get your agent(s)

| Your test needs… | Use | Inject |
|---|---|---|
| one named adapter | `@with_adapters(Adapter.ANTHROPIC)` | `agent` — a `ProvisionedAgent` |
| several named adapters in one room | `@with_adapters(Adapter.LANGGRAPH, Adapter.ANTHROPIC)` | `agents` — list; `a, b = agents` |
| two of the **same** adapter | `@with_adapters(Adapter.ANTHROPIC, Adapter.ANTHROPIC)` | `agents` |
| the **same scenario across every adapter** | `@per_adapter()` (the full matrix, explicitly) | `agent` (id via `agent.adapter_id`) |
| a **subset** of adapters | `@per_adapter(Adapter.X, Adapter.Y)` (positional = include) / `@per_adapter(exclude={...} / supports={Capability.MEMORY} / without={Capability.MEMORY} / runs_tool_loop=True)` | `agent` |
| to **drive the lifecycle yourself** (build-only, reboot, rehydration) | `@per_adapter()` | `cell` — an `AdapterCell` (see **AdapterCell** below) |
| a **fanned cell A + one different-framework peer B** (cross-framework) | `@per_adapter(exclude={Adapter.X}, peer=Adapter.X)` | `cell` (A) + `peer` (B, an `AdapterCell` you drive); peer deps fold into the cell's `@requires` |
| custom tools (any tool-capable framework) | `@with_adapters(Adapter.X, tools=[LOOKUP_TOOL], **EXECUTION_REPORTING)` — one `ToolSpec`, translated per framework (anthropic-family, pydantic-ai, agno) | `agent` |
| custom tools across the matrix | `@per_adapter(Adapter.ANTHROPIC, Adapter.PYDANTIC_AI, Adapter.AGNO, tools=[LOOKUP_TOOL], **EXECUTION_REPORTING)` | `agent` |

- **Reference adapters by the typed `Adapter` enum, never a string.**
- **Steer construction with a shape, don't re-spell args:** `@with_adapters(Adapter.X, **TOOL_AGENT)`
  (exact-tool-execution prompt) or `**MEMORY_AGENT` (that prompt + memory tools
  surfaced as `tool_call` events). `@per_adapter(..., **MEMORY_AGENT)` works too —
  the steering (`prompt` / `features` / `tools`) rides on the decorator and is
  carried per-cell as the `agent` / `cell` defaults.
- `agent` is the cell's (or slot's) running `ProvisionedAgent`; read its id off
  `agent.adapter_id` (no separate `adapter_id` fixture to request, no unpacking).
- **Custom tools:** define a tool once as a `ToolSpec` (input model + handler) and
  pass `tools=[LOOKUP_TOOL]` to `@with_adapters` / `@per_adapter`; the builders
  translate it to each framework's native form (band `CustomToolDef`, a pydantic-ai
  `RunContext` callable, an agno tool). Add `**EXECUTION_REPORTING` to observe the
  calls via `capture.tool_calls`. Only letta can't accept a local tool (MCP) and
  **rejects** `tools` with a clear error rather than silently dropping it.
- **Cross-framework peer:** `@per_adapter(exclude={Adapter.X}, peer=Adapter.X)` fans A
  across the matrix and hands each cell a *different-framework* peer B via the `peer`
  fixture (an `AdapterCell` the test drives — provision + `run_as`). The peer's
  `@requires` fold into the cell's single gate mark, and the peer is visible to lane
  scheduling (so A + B must be lane-hostable together — see **CI lanes** below).
  `@per_adapter` rejects a peer that is not a **live** (non-`e2e_pending`) adapter, and
  the `peer` fixture fails loud if the peer equals the cell (same framework, not cross).
  Worked example: `smoke/matrix/test_rehydration_cross_framework.py`.

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
- ❌ a separate `@requires` when `@with_adapters` / `@per_adapter` already gate it.
- ❌ a hand-rolled `@pytest.mark.parametrize("adapter_id", …)` matrix — use
  `@per_adapter` (the wiring guard blocks the hand-rolled form).
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
  `@with_adapters` / `@per_adapter`; waits go through the delivery barrier;
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
| `toolkit/provisioning.py` | `ResourceManager` (provision/reap agents + rooms, orphan sweep, `track_running` reboot-race guard; `.peer(agent)` mints a `PeerActor`), `AdapterCell` (build / provision / running / run_as — the per-cell lifecycle object behind `agent`/`cell`), `running_agent` (run an already-provisioned identity — enter twice against one identity for a rejoin), `running_provisioned_agent` (provision + run, composes `running_agent`), `ProvisionedAgent` (`.adapter_id` records the cell/slot it came from), `PeerActor` (act as a peer agent — post one message as that agent via its own key; the Agent-API twin of `UserOps`, used for the L0/L4 `Echo` peer) |
| `toolkit/adapters.py` | registry core: `Adapter` enum (the **one** source of adapter ids), the `@adapter` decorator + registry, `spec_for`, `build_adapter`, `specs`, `adapter_lane`, the discovery guard |
| `toolkit/builders.py` | the per-framework `@adapter` builder functions (one `_build_*` each); imported by `adapters.py` for the registration side-effect — no public API. Add a new adapter's builder here (see `ADDING_AN_ADAPTER.md`) |
| `toolkit/ci_lanes.py` | the CI-lane partition + workflow-drift guards: `CILane`, `ci_lanes`, `hosting_lanes` (which lanes' `uv` extra can host a framework), `assert_every_adapter_has_a_ci_home`, `assert_workflow_lane_*` |
| `toolkit/tools.py` | `ToolSpec` — define a custom tool **once** (input model + handler); the builders translate it to each framework's native form |
| `agents.py` | topology decorators: `@with_adapters(Adapter.X, ...)` (fixed set / one room → `agent`/`agents`), `@per_adapter(*adapters, exclude/supports/without/runs_tool_loop, prompt=, features=, tools=)` (fan across the matrix/subset → `agent`/`cell`); `adapter_params` is the internal parameter source `@per_adapter` feeds to the `adapter_id` fixture |
| `smoke/samples/sample_agents.py` | shared driving glue: the role-setting `TOOL_AGENT_SYSTEM_PROMPT`, `memory_features()`, reusable **agent shapes** (`TOOL_AGENT`, `MEMORY_AGENT`) for `@with_adapters(..., **SHAPE)` / `@per_adapter(..., **SHAPE)`, and the `*_instruction(...)` builders |
| `fixtures/agents.py` | the agent fixtures: `agent` (single running `ProvisionedAgent`), `agents` (the `@with_adapters` group), `cell` (an `AdapterCell` to drive yourself), `adapter_id` (internal `@per_adapter` parametrize target) |
| `agent_wiring.py` | `assert_agent_fixtures_wired` — the collection-time guard that rejects mis-wired decorator/fixture pairings (see **Wiring fences**) |
| `smoke/samples/sample_tools.py` | sample custom tools as `ToolSpec`s (`LOOKUP_TOOL`, `WEATHER_TOOL`), prompts, and the `EXECUTION_REPORTING` shape |
| `toolkit/user_ops.py` | `UserOps`: act as the test user (send message, create/delete room, add/remove/list participants, list messages/events, `lookup_peers` — the invitable roster) |
| `toolkit/capture.py` | `ReplyCapture` (subscribe-before-send), `reply_capture` ctx, `wait_for_processed` (delivery-status barrier), `tool_calls()`/`thoughts()`/`errors()`/`tasks()`/`events()`/`memory(agent)`, `CaptureFactory` |
| `toolkit/requirements.py` | pytest-free requirement facts: `Dep` enum, `DepSpec` predicates, `Lane`/`LANE_EXTRAS`, `dep_lane` (the **one** source of the `Dep`/lane facts the registry references without importing pytest) |
| `toolkit/judge.py` | `judge()` LLM-as-judge, `Verdict`, `format_transcript` |
| `toolkit/observations/` | list subclasses that own their assertions: `Replies` (replies; `snapshot()`/`since()`, `mentioning(id)` to filter to replies mentioning a participant, `assert_contains_any`/`assert_mentions`), `ToolCalls`/`ToolCall` + `MemoryToolCalls`, `Events`→`Thoughts`/`Errors`/`Tasks`, `Memories`/`MemoryObservation`; shared `tolerant_match` + `ContentAssertions` |
| `settings.py` | `BaselineSettings`: endpoints, credentials, run policy, LLM creds + models |
| `requires.py` | `@requires(Dep.X)` decorator (the pytest glue; re-exports `Dep` from `toolkit/requirements.py`, where the enum and its facts actually live) |
| `conftest.py` | fixtures (below) + the always-on E2E gate |
| `guards/` | E2E-tree harness self-tests (E2E-gated): `test_adapter_registry.py` (static discovery/lane guard), `test_provisioning.py`, `test_user_ops.py`, `test_adapter_cell.py`, `test_tool_spec.py`, and `test_agent_wiring.py` (only the `pytester` real-collection cases). **Pure policy tests run every PR from `tests/framework_conformance/`** — see the note below the table |
| `smoke/` | proof tests that exercise the tools end to end — read these as worked examples — grouped by subject (below) |
| `smoke/samples/` | shared driving glue (not tests): `sample_agents.py`, `sample_tools.py` |
| `smoke/matrix/` | runs across the adapter matrix: `test_adapter_matrix.py`, `test_capability_matrix.py` (memory store + recall), `test_context_recall.py` (in-session + rejoin), `test_rehydration_offline.py` / `test_rehydration_partial.py` (cold-boot / partial-reboot `/context` recall), `test_rehydration_cross_framework.py` (a different-framework `peer=` authors, A rehydrates), `test_room_isolation.py`, `test_noisy_room.py`, `test_tool_round_trip.py` (custom-tool subgroup) |
| `smoke/behavior/` | platform/transport + scenario behavior: `test_delivery_status.py`, `test_processing_barrier.py`, `test_isolation.py`, `test_agent_scenarios.py` |
| `smoke/inspection/` | `capture.*` observation worked-examples: `test_tool_calls.py`, `test_events.py`, `test_memory.py` |
| `smoke/adapters/` | adapter-specific showcases: `test_agno.py`, `test_crewai.py`, `test_letta.py`, `test_parlant.py` |

The `toolkit/` modules are pytest-free and reusable anywhere. The package root
(`settings`, `requires`, `agents`, `conftest`) is the pytest wiring.

**Pure policy tests run on every PR — put them outside this tree.** The whole
`tests/e2e/**` tree is skipped unless `E2E_TESTS_ENABLED=true` (which PR CI does not
set), so a pure-logic guard placed here protects nothing on PRs. The collection-time
policy tests therefore live in **`tests/framework_conformance/`** (no platform, no
keys, run every PR): `test_lane_scheduling.py` (the derive-then-guard scheduling +
`hosting_lanes` rules), `test_e2e_lane_drift.py` (the `@lane`/workflow drift guards),
and `test_agent_wiring_rules.py` (the `assert_agent_fixtures_wired` policy). When you
add a new pure guard/policy check, put its unit tests there; keep only
fixture-closure / platform-touching cases under `guards/`.

## Fixtures (from `conftest.py`)

`baseline_settings`, `user_ops`, `resource_manager`, `reply_capture`, `judge`,
`agent`, `agents`, `cell`, `adapter_id`, `baseline_ws`.

- `reply_capture` and `judge` pre-bind their plumbing (the WS observer; the judge
  model + key), so tests pass only the test-specific arguments.
- `agent` is the single running `ProvisionedAgent` — sourced from `@per_adapter`
  (the current cell) **or** `@with_adapters(OneAdapter)`. Its id is `agent.adapter_id`.
- `agents` is the running group declared by `@with_adapters(A, B, …)`, in declared
  order; each carries its own `.adapter_id`.
- `cell` is the `@per_adapter` cell's `AdapterCell` — request it (instead of `agent`)
  when the test drives its own lifecycle (a build-only check, or a reboot /
  rehydration scenario). See **AdapterCell** below.
- All three build/provision/run through the registry and are reaped on teardown; the
  decorator also auto-applies the requirement gate.
- The old per-cell agent fixture is **gone**, and so is the old "bare fixture
  request = full matrix" path — a full-matrix test is now written `@per_adapter()`
  and reads `agent` / `agent.adapter_id`.
- `adapter_id` is the internal parametrize target `@per_adapter` fans over; it is
  normalized to `str`. Live tests read `agent.adapter_id`, manual tests
  `cell.adapter_id` — you rarely request `adapter_id` directly.
- The E2E + Band-key gate is applied to every baseline test automatically, so a
  gate-only test needs no decorator.

## AdapterCell: driving the lifecycle yourself

Under `@per_adapter()`, request **`cell`** (an `AdapterCell`) instead of `agent`
when the test owns the agent's start/stop — a no-provision construction check, or a
reboot / restart / rehydration scenario. `agent` is just sugar over `cell.running()`;
the decorator's `prompt` / `features` / `tools` steering is carried on the cell as
defaults (a method argument overrides).

| Method | Does | Provisions? | Runs? |
|---|---|---|---|
| `cell.build()` | constructs the adapter without running it | no | no |
| `await cell.provision(label=…)` | registers a tracked+reaped identity (`ProvisionedAgent`) | yes | no |
| `async with cell.run_as(identity)` | runs a *fresh* adapter under an existing identity | no | yes |
| `async with cell.running(label=…)` | provision **and** run in one step (what `agent` uses) | yes | yes |

Build-only (cheap, sync test):

```python notest
@per_adapter()
def test_build(cell):
    assert isinstance(cell.build(), SimpleAdapter)   # no network, no provisioning
```

Reboot / rehydration — provision once, enter `run_as` **twice** (stop → fresh run
under the same identity), so a correct recall in run 2 can only have come from the
platform rehydrating the room, not in-memory adapter state:

```python notest
@per_adapter(prompt=REPLY_PROMPT)
async def test_recalls_after_rejoin(cell, resource_manager, user_ops, reply_capture):
    identity = await cell.provision(f"rejoin-{cell.adapter_id}")
    room_id = await resource_manager.provision_room(participants=[identity.id])

    async with cell.run_as(identity):          # run 1: state a note, then stop
        async with reply_capture(room_id) as capture:
            mid = await user_ops.send_message(room_id, REMEMBER, mention_id=identity.id, mention_name=identity.name)
            await capture.wait_for_processed(mid, identity.id)

    async with cell.run_as(identity):          # run 2: fresh adapter, same identity
        async with reply_capture(room_id) as capture:
            mark = capture.messages.snapshot()
            mid = await user_ops.send_message(room_id, RECALL, mention_id=identity.id, mention_name=identity.name)
            await capture.wait_for_processed(mid, identity.id)
            capture.messages.since(mark).assert_contains_any([note])
```

Cross-framework peer — fan cell **A** across the matrix and drive a *different-framework*
peer **B** (via the `peer` fixture) in the same room; `.mentioning()` filters a capture
to replies that mention a participant. See `smoke/matrix/test_rehydration_cross_framework.py`:

```python notest
@per_adapter(exclude={Adapter.LANGGRAPH}, peer=Adapter.LANGGRAPH, prompt=REPLY_PROMPT)
async def test_foreign_peer(cell, peer, resource_manager, user_ops, reply_capture):
    marker = unique_marker("note")
    a = await cell.provision(f"a-{cell.adapter_id}")          # A (fanned)
    b = await peer.provision(f"b-{peer.adapter_id}")          # B (a different framework)
    room_id = await resource_manager.provision_room(participants=[a.id, b.id])

    # B authors one message mentioning A that carries the marker (so it enters A's
    # agent-scoped /context), then stops. (The real test factors this into a helper.)
    b_prompt = f"{REPLY_PROMPT} Send exactly one message that mentions {a.name} and contains this token: {marker}"
    async with peer.run_as(b, prompt=b_prompt):
        async with reply_capture(room_id) as capture:
            probe = capture.messages.snapshot()
            mid = await user_ops.send_message(room_id, "pass a note", mention_id=b.id, mention_name=b.name)
            await capture.wait_for_processed(mid, b.id)
            capture.messages.since(probe).mentioning(a.id).assert_contains_any([marker])  # setup precondition

    async with cell.run_as(a):                                   # A cold-boots → rehydrates B's message
        async with reply_capture(room_id) as capture:
            mark = capture.messages.snapshot()
            mid = await user_ops.send_message(room_id, "what token did they send?", mention_id=a.id, mention_name=a.name)
            await capture.wait_for_processed(mid, a.id)
            capture.messages.since(mark).assert_contains_any([marker])
```

## Wiring fences (fail at collection, before any live agent)

The topology is guarded two ways so a mis-wired test never false-greens:

- **`assert_agent_fixtures_wired`** (`agent_wiring.py`, run from the collection hook)
  raises a `UsageError` for any of: `cell` requested without `@per_adapter`;
  `agent`/`agents` requested with no decorator; `agents` under `@per_adapter` (a
  cell is one adapter — use `agent`); `cell` under `@with_adapters` (fan-only);
  both decorators on one test; `agent` and `cell` both requested; a hand-rolled
  `parametrize("adapter_id")` with no `@per_adapter`.
- **`@per_adapter` raises at import** if its filters select **no** adapters (an
  empty `parametrize` would skip silently — fail-loud forbids that). A bare
  `@per_adapter()` is never empty.
- **`ResourceManager.track_running`** raises if one identity is already running,
  blocking overlapping/nested runs of a single identity (the classic reboot bug);
  the id is released in `finally`, so a failed startup never wedges it.

## "I want to..." -> use this (do not reinvent)

| Need | Use |
|------|-----|
| Run a specific named agent | `@with_adapters(Adapter.ANTHROPIC)` → `agent` (or `agents` for several); auto-gates + runs + reaps |
| Run a standard prompt/features shape | `@with_adapters(Adapter.X, **TOOL_AGENT)` / `**MEMORY_AGENT` — don't re-spell `prompt=`/`features=` |
| Run the same scenario across every adapter | `@per_adapter()` → `agent` (id via `agent.adapter_id`) |
| Run a scenario across a subset | `@per_adapter(Adapter.X, Adapter.Y)` (positional = include) / `@per_adapter(exclude= by id, supports=/without={Capability.MEMORY} by capability, or runs_tool_loop=True for the custom-tool subgroup)`; add `prompt=`/`features=` (or `**MEMORY_AGENT`) to steer |
| Drive the agent lifecycle myself (build-only / reboot / rehydration) | `@per_adapter()` → `cell` (`cell.build()` / `cell.provision()` / `cell.run_as()` — see **AdapterCell**) |
| Clean up what I created | nothing: `resource_manager` reaps on teardown (`BAND_E2E_AUTOCLEAN=false` keeps it for debugging) |
| Drive the platform as a user | the `user_ops` fixture (`UserOps`) |
| List who the user could invite to a room | `await user_ops.lookup_peers(not_in_room=room_id)` → `list[Peer]` (the invitable roster; `peer_type="Agent"` narrows) |
| Drive a peer agent (e.g. the `Echo` bounce) | `await resource_manager.peer(peer).send_message(room_id, "ECHO: ...", mention_id=agent.id, mention_name=agent.name)` — posts as that agent, returns the message id to barrier on (needs the peer already in the room) |
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
| Declare extra requirements explicitly | `@requires(Dep.OPENAI, ...)` (missing one **fails**) — but `@with_adapters`/`@per_adapter` already do this for the agents they build |

## Assertion strategy: cheap checks first, judge last

Prefer the cheapest assertion that proves the point. The LLM judge costs tokens,
adds latency, and is itself non-deterministic, so do not reach for it by reflex.

1. Structural facts -> the tolerant assertion methods on `capture.messages`
   (`Replies`): `assert_present`, `assert_at_least`, `assert_contains_any`,
   `assert_contains_none` (the tolerant negative — no forbidden value present),
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
non-default lane among its deps). `ci_lanes()` (`toolkit/ci_lanes.py`) groups every
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
| `letta` | `dev` | letta | none yet — Letta is `e2e_pending` (no live tests; see "Letta is out of scope" below) |

`backends` folds codex + opencode into one job (both install `dev`, differ only in
the backend their job stands up) so a job-per-backend isn't needed; the cost is that
one backend failing to come up can redden the other's cells (the per-adapter report
still shows which). `google` gets its own lane so Google free-tier rate-limit
flakiness is isolated; `letta` stands alone for the live-server backend it will need.

**The knob:** `BAND_E2E_LANE=<lane id>`. Scheduling is *derived*: a test's lane is the
home lane of **all** the frameworks it touches — a matrix cell's adapter (plus its
`peer=`, if any), or a `@with_adapters` group's set. When `BAND_E2E_LANE` is set,
`lane_selection.apply_lane_skips` (called by the conftest hook) marks
**skip-with-reason** every test whose (single) home lane isn't the active one. An
**in-lane** adapter is left untouched, so a missing key/CLI/server still **fails** via
its `@requires` gate (an unwired lane is red by design until its setup lands).
Adapter-agnostic tests always run. **Unset** (the local default) runs the full matrix,
fail-loud.

**Home ≠ hosting.** An adapter's *home* lane is where its single-framework cells run;
*hosting* is which lanes' `uv` extra can actually install a framework (`hosting_lanes`
in `toolkit/ci_lanes.py`: every `dev` lane hosts every `dev` framework; `dev-crewai`
hosts only the crewai stack). A test whose frameworks share one home lane runs there.
A test whose frameworks span **more than one** home lane is hosted by no single job by
default, so `assert_every_item_is_schedulable` (the same hook) **fails collection** for
it — the fail-loud guard against a test that would silently skip in every lane (false
green). The escape hatch is `@lane(Lane.X)` (from `agents`), which pins such a test to
one lane — and the guard **validates** that `Lane.X` actually *hosts* all its
frameworks, so a typo'd, unknown, or wrong-extra pin fails collection too (not a
silent skip). A single-framework cell never trips this; a same-lane `peer=` (e.g. two
`core` frameworks, as in `smoke/matrix/test_rehydration_cross_framework.py`) is
schedulable as-is.

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
   `$GITHUB_ENV`; see the codex/opencode scripts) and a step that runs it
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

## Letta is out of scope (this PR)

Letta has **no live E2E here** — its smokes are deferred to a follow-up. Letta is
server-side and executes platform tools only by calling a band-mcp server, and
standing one up reachable from the Letta server (its SSRF guard rejects a loopback
MCP URL) isn't wired yet.

So the Letta adapter is registered **`e2e_pending`** (`toolkit/adapters.py`): it
still *defines* the `letta` CI lane via `ci_lanes()`, but `specs()`/`adapter_params()`
exclude it, so it is **not a matrix cell** and no `@with_adapters(Adapter.LETTA)`
tests run. The `letta` lane therefore runs only the adapter-agnostic baseline tests
plus one placeholder (`smoke/adapters/test_letta.py`), and needs no backend setup.

**To re-enable Letta** once band-mcp reachability is solved: flip `e2e_pending` to
`False` (or drop the kwarg) on the `@adapter(Adapter.LETTA, …)` registration — that
alone returns Letta to the matrix — then add the live smokes and the lane's band-mcp
setup. The builder is kept valid for that day.
