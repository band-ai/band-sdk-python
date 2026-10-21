# Adapter plan template

Before writing any code, produce a plan (a Linear attachment per
`band-plan/publish.md`, or a file outside the tracked docs, which run as
tests) by copying the skeleton below and filling every
section from real research. Sections you cannot fill are research you have
not done yet.

````markdown
# <framework> adapter — feature map, gaps, and implementation plan

> ⚠️ **Ground truth is the installed package, not this document.** Before
> writing adapter code, install the SDK and inspect it:
> `uv run --all-packages python -c "import <pkg>, inspect; print(sorted(dir(<pkg>)))"` and
> `inspect.signature(...)` on every entry point you plan to call. Treat every
> API snippet below as provisional and correct it to the installed version.
> Where this doc says "verify", that is a hard gate, not a suggestion.

## Context
What the ticket asks for; which harness family the framework belongs to
(subprocess runtime like claude_sdk/codex vs. direct-API like anthropic vs.
graph framework like langgraph); the closest existing template adapter to
mirror; the injection seam where the model's tool decision enters our code.

## SDK documentation map
A URL table organized by *what you're implementing* (not alphabetically), so
future lookups are one jump. **Bold the rows needed for the core phase.**
The SDK's source/README is the one primary source; docs pages are secondary.

| When you're working on… | Doc page | URL |
|---|---|---|
| **Session create/resume, persistence** | … | … |
| **Event stream & event data classes** | … | … |
| **Custom tools & permissions** | … | … |
| Auth / setup / BYOK / streaming / hooks (phase-B rows) | … | … |

## Capability map (Band requirement → SDK feature)
Legend: **E** = easy/core (this PR), **M** = medium (this PR, needs care),
**D** = defer.

| Band requirement | SDK feature that satisfies it | Effort | Notes / pitfalls |
|---|---|---|---|
| Reply to a room message | … | E | Plain assistant text is NOT auto-delivered; the model replies via `band_send_message`, the adapter relays final text only through `relay_reply` and posts its own messages with `send_notice` (`AGENTS.md` → "Turn Outcome") |
| System prompt / `custom_section` | … | E | Use `render_system_prompt(...)`; own the prompt contract |
| Model selection | … | E | Verify whether the SDK requires a model; use `core/defaultmodels.py` for shared defaults, or `None` only when the framework supports its own default |
| Expose Band platform tools | … | E | Bridge from `tools.get_openai_tool_schemas(...)` + `filter_tool_schemas` |
| Developer custom tools | … | E | `custom_tools_to_schemas` / `find_custom_tool` / `execute_custom_tool`; first-wins dedup |
| `Capability.MEMORY` / `CONTACTS` | … | E | Gate schemas + prompt sections; Band memory ≠ framework-native memory |
| History conversion | … | E | `[sender]: content` text or structured list; own-reply handling (see gaps) |
| **Context rehydration** (cross-restart) | … | M | Convert platform history; where native sessions exist, evaluate persisted resume ids plus a history fallback |
| **Emitted events** (`TOOL_CALLS`/`THOUGHTS`) | … | M | Emit from our handlers where we control execution |
| Per-room isolation | … | E | Isolate room state; use separate native sessions where the SDK exposes them |
| Cleanup on room leave / shutdown | … | E | Per-room cleanup + adapter-wide `cleanup_all` |
| Testability | … | E | Inject the actual external boundary (client, graph, runtime, or factory) for realistic fakes |
| Human-in-the-loop ("ask the user" tool) | … | M | Route to the room via turn-splitting — see Gaps below |
| (Framework-specific extras) | … | D | Defer until a customer needs it |

## Gaps & pitfalls (the four recurring focus areas)
- **Context rehydration**: platform `/context` history is agent-scoped. For
  stateless APIs, convert that history into requests. Where native resume is
  supported, verify its persistence boundary and decide how to record resume
  ids and fall back to history when the session is unavailable.
- **Emitted events**: bridged-tool execution we control → emit `tool_call`/
  `tool_result` from our handlers (payload keys `name`/`args`/`output`/
  `tool_call_id` — `ToolEventKey` in `core/types.py`, parsed by
  `converters/parsing.py`). Framework-native
  tool events differ in shape; decide now or defer explicitly. Reasoning may
  be absent for non-reasoning models — guard `Emit.THOUGHTS`.
- **Two things called "memory"**: Band platform memory (`band_*` tools) vs.
  the framework's native memory. Keep them orthogonal; never conflate.
- **Interactive "ask the user" tools**: if the framework can pause a turn to
  ask a human (blocking callback/RPC), ground-truth its semantics before
  designing — handler timeout? cancellation on abort? does the pending
  request survive session resume? — and hold it against Band's side: each
  room's messages are processed strictly serially, so a turn that blocks
  waiting for a *future room message* deadlocks until the turn timeout. The
  shape that fits both is **turn-splitting**: post the question to the room,
  resolve the tool call immediately with a delivery-ack sentinel, and let
  the answer arrive as the next normal turn on the persisted session. Route
  questions to where the humans are — in Band that is the room; a
  terminal/host-process handler is an operator special case, never the
  default — and keep deployment assumptions out of prompt text ("an operator
  is watching your terminal" steers the model wrong).
Also list: runtime/auth prerequisites for live testing, Python-version floor,
optional-import guard plan, and any concurrency rate-limits documented
upstream.

## Session architecture & per-room isolation

First identify the framework's state boundary. Stateless APIs need isolated
request/history state; graph frameworks may own checkpoints; persistent
runtimes may need the session layout below. Do not introduce a session
manager, synthetic ids, or a subprocess lifecycle without a verified need.

For a runtime that supports persistent sessions:
```
1 adapter instance ── owns ──▶ 1 client/runtime
        │
        └── per room_id ──▶ 1 session/thread (id = deterministic, per (agent, room))
                                ├── conversation history (isolated per session)
                                └── framework-native state (isolated per session)
```
- When the SDK accepts caller-chosen session ids, fold in the **immutable Band agent id**
  (`self.platform.agent_id`, the `PlatformConnection` the runtime injects
  before `on_started`) —
  never the display name (renameable, not guaranteed unique). Scheme:
  `band-{agent_id}-{room_id}`; explicit prefix overrides. When it assigns ids,
  retain those ids in the room mapping instead.
- Cross-agent collision analysis: what happens when two agents share a host,
  a state directory, or one client? Name the mitigation.
- Concurrency: which calls may interleave? Trace existing runtime
  serialization before adding locks; preserve room isolation for concurrent
  rooms and cleanup.

## Tool bridging & feature-collision policy
Long-lived handlers must resolve the current room's `AgentToolsProtocol` at
call time, since their sessions outlive a message. A handler scoped to one
turn can use that turn's tools directly. Then classify every Band↔framework overlap:

| Collision | Handling |
|---|---|
| Mutually-exclusive / invalid config | fail construction: invalid settings in the config's validators; conflicting live objects with `BandConfigError` in the ctor |
| Band feature merely overlaps a framework feature | warn-and-degrade (`UserWarning` + log) |
| Unsupported `emit`/`capability` values | free via `SUPPORTED_*` (`SimpleAdapter.__init__` raises `BandConfigError`) |
| Framework built-in tools (shell/file) | default OFF; permissive auto-approval is only safe while they are off |
| Tool-name duplicates | first-wins dedup, silent |

## Implementation spec (core phase)
Numbered file checklist of the registration spots (converter, adapter,
`__init__` lazy imports, pyproject extra + dev, conformance configs, output
adapter, unit tests, example, E2E registry) — `AGENTS.md` → "Adding a New
Framework Integration" says which guards name a missing one. Add here only the
adapter-specific decisions: ctor/config fields, session-manager API, turn
loop shape, eviction policy.

## Phasing
Core now; defer the rest until a customer needs it. Definition of Done:
- [ ] converter + adapter + any required state owner implemented and registered
- [ ] conformance configs registered; config-drift green
- [ ] unit tests green (converter + adapter incl. per-room isolation, resume,
      failure paths); no over-mocking
- [ ] runnable example(s) under `examples/<framework>/`
- [ ] E2E registry entry (+ dedicated live tests where the adapter has
      unique behavior)
- [ ] `ruff check`, `ruff format`, `pyrefly check` clean; full unit suite green
- [ ] live-verified against the real runtime (happy path + one failure path)

## Engineering principles
Simplicity first; short single-concern functions; typed everything; comments
capture decisions (WHY), never mechanics; logging is leveled and traceable
per room lifecycle. See `SKILL.md` "Phase 4 — Quality bar" for the concrete quality bar.
````
