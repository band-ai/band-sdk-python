---
name: band-add-framework-adapter
description: Use this skill when the user asks to add a new framework adapter to the Band Python SDK — "add an adapter for framework X", "integrate SDK Y as an adapter", "implement the Z adapter from this ticket". Takes a ticket + context and drives the full process — parallel research (repo patterns + online SDK source), installed-package ground-truthing as a hard gate, a fillable plan template, a five-question plan gate, TDD implementation, the concrete quality bar (config per the Adapter Constructor Shape, shared constants, no bolt-ons), scrutinized review rounds, live runtime probes, live E2E, and ship gates.
---

# Adding a Framework Adapter — plan template & execution playbook

A skill-style instruction document for an AI coding agent (or a developer)
tasked with adding a new framework adapter to this SDK. Input: a ticket plus
whatever context the developer supplies ("add an adapter for framework X").
Output: a researched plan, a reviewed implementation, meaningful tests, live
verification, and a shipped PR — at the quality bar this process was distilled
from.

The document has two halves:

1. **The plan template** — produce a per-adapter plan document *first*, by
   filling the template below. Research feeds the template; the template gates
   the code.
2. **The execution process** — the phases to run, in order, with the quality
   standards and review discipline to apply.

Cross-reference: the mechanical registration steps live in `AGENTS.md` →
"Adding a New Framework Integration" (the `tests/framework_conformance/`
drift guards name whatever is still unregistered) and
`tests/e2e/baseline/ADDING_AN_ADAPTER.md` (the E2E matrix). This document
does not duplicate them; it tells you how to *research, decide, verify, and
review* around them.

North star: **research before code, ground truth over documentation, reuse
over reinvention, and clean & elegant over fast**. A smaller adapter that
holds its invariants beats a feature-complete one that leaks state.

---

# Half 1 — The plan template

Before writing any code, produce `docs/<framework>-adapter-plan.md` (or a
ticket/Linear document) by copying the skeleton below and filling every
section from real research. Sections you cannot fill are research you have
not done yet.

````markdown
# <framework> adapter — feature map, gaps, and implementation plan

> ⚠️ **Ground truth is the installed package, not this document.** Before
> writing adapter code, install the SDK and inspect it:
> `python -c "import <pkg>, inspect; print(sorted(dir(<pkg>)))"` and
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
| Model selection | … | E | Default None → framework default; explicit override via config |
| Expose Band platform tools | … | E | Bridge from `tools.get_openai_tool_schemas(...)` + `filter_tool_schemas` |
| Developer custom tools | … | E | `custom_tools_to_schemas` / `find_custom_tool` / `execute_custom_tool`; first-wins dedup |
| `Capability.MEMORY` / `CONTACTS` | … | E | Gate schemas + prompt sections; Band memory ≠ framework-native memory |
| History conversion | … | E | `[sender]: content` text or structured list; own-reply handling (see gaps) |
| **Context rehydration** (cross-restart) | … | M | Persist a resume id via `task` event metadata + text-injection fallback |
| **Emitted events** (`TOOL_CALLS`/`THOUGHTS`) | … | M | Emit from our handlers where we control execution |
| Per-room isolation | … | E | One session/thread per room; deterministic ids |
| Cleanup on room leave / shutdown | … | E | Per-room cleanup + adapter-wide `cleanup_all` |
| Testability | … | E | `client_factory` seam for hand-rolled fakes |
| Human-in-the-loop ("ask the user" tool) | … | M | Route to the room via turn-splitting — see Gaps below |
| (Framework-specific extras) | … | D | Defer until a customer needs it |

## Gaps & pitfalls (the four recurring focus areas)
- **Context rehydration**: native resume is usually host/disk-local. Plan the
  two-tier scheme: persist the resume id in `task`-event metadata (converter
  reads it back) AND build a text-history fallback for resume misses. Note:
  platform `/context` history is agent-scoped.
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
```
1 adapter instance ── owns ──▶ 1 client/runtime
        │
        └── per room_id ──▶ 1 session/thread (id = deterministic, per (agent, room))
                                ├── conversation history (isolated per session)
                                └── framework-native state (isolated per session)
```
- Deterministic session id: fold in the **immutable Band agent id**
  (`self.platform.agent_id`, the `PlatformConnection` the runtime injects
  before `on_started`) —
  never the display name (renameable, not guaranteed unique). Scheme:
  `band-{agent_id}-{room_id}`; explicit prefix overrides.
- Cross-agent collision analysis: what happens when two agents share a host,
  a state directory, or one client? Name the mitigation.
- Concurrency: which calls may interleave? Usually: rooms in parallel, one
  room's calls serialized by a per-room lock owned by the session manager.

## Tool bridging & feature-collision policy
Handlers must resolve the current room's `AgentToolsProtocol` at call time
(`_room_tools[room_id]`), never a captured reference — sessions outlive any
single message. Then classify every Band↔framework overlap:

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
- [ ] converter + session manager + adapter implemented and registered
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
per room lifecycle. See Half 2 §4 for the concrete quality bar.
````

---

# Half 2 — The execution process

## Team & roles

Run the work as roles. An orchestrating session may map them to subagents;
a single implementing agent treats them as **review passes to run**, not
subagents to spawn.

| Role | Responsibility | Mapped to |
|---|---|---|
| Project manager | Sequences phases, holds scope (core only), tracks tasks | main session + task tracking |
| Architect | Guards the client/session-per-room contract; no cross-room leakage | `Plan` agent at phase boundaries |
| Developer(s) | Implements slices (converter, manager, adapter, tests, example) | `Explore`/general agents per independent slice |
| Principal | Guards simplicity, typing, naming, comment discipline | `/code-review` (high) + focused quality pass |
| Peer review | Verifies diff logic (bridging, resume/fallback, isolation) | `/code-review` and/or a fork |
| QA | Runs suites, triages failures, owns flake policy | `/band-write-e2e-test`, `/band-investigate-test-error`, live runs |

Cadence: plan a slice → implement → review → test → next. Keep slices small
so review stays cheap.

## Phase 1 — Research (parallel, before any plan)

Launch in one batch:

1. **Repo pattern maps** (Explore agents): the closest template adapters
   (which family? subprocess-runtime adapters mirror claude_sdk/codex;
   direct-API mirror anthropic/gemini), the conformance registries, and a
   **test-infrastructure reality check**. Do not trust docs, memories, or
   design notes about test infra — read the actual branch. Registration steps
   and helpers named in older notes are often renamed or gone.
2. **SDK ground truth online**: WebFetch the SDK's *source* (README plus the
   actual `client.py`/`session.py`/`tools.py` files), not just docs pages.
   Docs lie by omission and by staleness; note every discrepancy.

Then the **hard gate — step 0 of implementation**: add the pyproject extra,
`uv sync`, and inspect the installed package with `inspect.signature(...)` on
every call you plan to make. Verify EVERY claim your research produced.
Expect surprises; in past runs these included: resume accepting the full
tool-registration kwargs; a provider config field documented as optional but
required by the runtime; a turn timeout that does *not* abort in-flight work
(an explicit abort call does); and crash-vs-clean-stop revival differing
(auto-revive worked only after a clean stop — a crashed subprocess needed an
explicit `start()`); and an ask-the-user callback whose pending request had
no timeout, no cancellation on abort, and was not replayed on session
resume. Each of these changed the design. When behavior is
ambiguous, **probe it empirically** (spawn it, kill it, time it) — a
ten-line script beats an hour of doc reading.

## Phase 2 — Plan gate

Fill the Half-1 template into a plan file. Before coding, answer five
questions honestly; any "no" means fix the plan:

1. **Reuse, not reinvention?** Every plumbing piece maps to an existing
   helper (`render_system_prompt`, `filter_tool_schemas`, `custom_tools_*`,
   `FakeAgentTools`, E2E fixtures). Grep before you write.
2. **Simple and elegant?** The design reads as "message in → turn → reply
   out"; anything clever needs a written justification.
3. **The real thing?** Decisions verified against the installed package, not
   docs.
4. **Meaningful scope?** Core phase names the regressions its tests catch.
5. **Not already covered?** Extend existing infrastructure (parametrized
   suites, shared constants) rather than adding parallel copies.

## Phase 3 — Implement (TDD order)

Follow the registration steps (`AGENTS.md` → "Adding a New Framework
Integration"), in this order, running the named suites after each step:

1. **Converter + unit tests** (pure Python — no SDK needed). Decide own-reply
   handling deliberately: when `band_send_message` calls go unreported
   (`Emit.TOOL_CALLS` off), the agent's own text messages are the *only*
   record of its side — the converter must include them or rehydration is
   one-sided.
2. **Conformance registration** (converter config, output adapter, then the
   adapter config once it exists) → `test_config_drift.py` green.
3. **Session manager + adapter.** Manager owns: client lifecycle, room→
   session registry, per-room turn locks (never dropped — lock identity must
   stay stable for queued waiters), lock-holding cleanup, and an
   abort-then-drop eviction for failed turns. Adapter owns: creation policy
   (resume vs create), tool bridging, prompt composition, event emission.
   Do not cache liveness the client can answer itself.
4. **Adapter unit tests** with a hand-rolled fake client/session at the
   `client_factory` seam (mirror `FakeCopilotClient` in
   `tests/adapters/copilot_sdk/fakes.py`) and the real
   `FakeAgentTools`. Never mock the thing under test. Cover: reply+mention;
   system-message assembly; bridged tool execution + events; custom-tool
   precedence; validation-error mapping;
   resume, resume-miss injection, eviction-then-resume; **two rooms → two
   sessions, no leakage**; cleanup per room and adapter-wide; startup-failure
   rollback.
5. **Registration** in both `__init__.py`s, pyproject extra + dev list.
6. **Example(s)** following `AGENTS.md` → "Example Files" (PEP 723 headers
   and the rest), `ValueError` for missing config,
   run-from-repo-root noted, and a README with prerequisites, feature-flag
   and model guidance, and a symptom→cause→fix troubleshooting table.
   If an example needs infrastructure to be correct (an instance lock, a
   log-gating ritual), the SDK is missing a feature — hoist it into the
   runtime behind a config default instead of shipping boilerplate users
   must copy.
7. **E2E registry entry** per `tests/e2e/baseline/ADDING_AN_ADAPTER.md` (all
   registration points — verify collection picks the new id up) plus
   dedicated live tests only for genuinely adapter-unique behavior.

## Phase 4 — Quality bar (concrete standards)

- **Constructor shape** (`AGENTS.md` → "Adapter Constructor Shape"):
  `<Framework>AdapterConfig` subclasses `BaseAdapterConfig` (or
  `EnvAdapterConfig` when env vars may set it) from
  `src/band/core/adapterconfig.py` — frozen, rejects unknown fields, plain
  data only so it loads from YAML/JSON. Validate settings in the config, not
  the constructor. The converter, custom tools and live objects (clients,
  factories, LLM objects, callbacks) stay keyword-only ctor args, plus
  `**features`. A routing policy is one argument (`None | "mode" |
  handler`), not parallel booleans/callbacks that can contradict each other.
  `tests/framework_conformance/adaptershape/test_adaptershape.py` enforces
  the shape.
- **No magic strings**: use `MessageType` (StrEnum) for event types; shared
  constants for cross-adapter facts (`ToolEventKey` in `core/types.py`);
  single-source metadata keys shared between adapter (writer) and converter
  (reader). Model-facing contract text (sentinel
  tool results, injected prompt guidance) is design surface, not string
  filler: shared constants, unit-tested, and taught in the system prompt —
  the model should not first learn a contract from its first tool result.
- **No `nonlocal`, no closure-smuggled state, no bolt-ons.** If a design
  forces a closure to leak results, restructure ownership instead (e.g. a
  registry `get`/`store` beats a factory callback returning through
  `nonlocal`). Flat methods with real parameters beat nested closures.
- **Explicit resource ownership**: injected client = borrowed (never stopped
  by the adapter); factory/default = owned (stopped at `cleanup_all`).
  Mutually-exclusive inputs raise `BandConfigError`.
- **Lifecycle discipline**: eager start + fail-fast auth probe in
  `on_started` *with rollback* (a failed start must not leak a subprocess);
  `cleanup_all` releases adapter-wide resources; failed/timed-out turns
  abort + evict the session so the next message starts clean.
- **PEP 257 docstrings** (summary on the first line, imperative mood);
  comments state constraints and WHY, never mechanics; `%s` logging with
  room/session ids traceable per lifecycle.
- Repo idioms from the `band-python-conventions` skill apply throughout
  (typing, no `print`, comprehensions over `continue`, no underscores in
  file or class names, no issue-tracker IDs in code).

## Phase 5 — Review rounds

1. Run **`/code-review` at high effort** on the branch; fix findings at the
   **root cause** — a fix that adds a branch to a tangled function is a
   smell; extract and clarify instead. Findings tend to cluster; a cluster in
   one area means redesign that area once, not N patches.
2. **Treat external reviews with scrutiny.** Validate every claim against
   the code — and empirically against the runtime — before agreeing. Reviews
   routinely contain both real catches and confidently-wrong claims (in past
   runs: a claimed lifecycle bug was real but the proposed fix was the wrong
   depth; another claim was refuted by reading the SDK's auto-revive path).
   Reject with evidence; accept with a plan.
3. Run a **documentation fact-check audit** as a separate pass: every
   load-bearing claim in docstrings, comments, and READMEs verified against
   the code and installed SDK. Stale comments are bugs waiting to mislead.

## Phase 6 — Verify live

1. **Direct runtime probes**: drive the real adapter with `FakeAgentTools`
   (no platform needed) — reply+mention, model-initiated bridged tool call
   with fallback suppression, cross-instance resume/recall, and at least one
   failure path (kill the subprocess; feed a bad token; exhaust a quota) to
   see the error surface a user will see.
2. **Live E2E** via the shared registry plus any dedicated tests, authored
   with `/band-write-e2e-test` discipline: listener opened before the trigger,
   event-driven waits (never `sleep`), behavioral assertions (substrings,
   floors — never exact counts or transcripts), credentials from settings
   with **fail-loud** on missing (only `E2E_TESTS_ENABLED` skips; see the
   validation policy in `tests/e2e/baseline/README.md`), reruns scoped to
   model nondeterminism. Provision rooms through the toolkit's
   `ResourceManager`, which reaps them on teardown.
   For features whose behavior spans turns (pending questions, resume,
   session state), cover the **multi-turn path**: real bugs often first
   surface on the *second* interaction — a follow-up mid-conversation —
   so a single-round-trip smoke proves too little.
   On failure, use `/band-investigate-test-error` before touching anything.

## Phase 7 — Gates & ship

- `uv run ruff check . && uv run ruff format . && uv run pyrefly check` and
  the full unit suite.
- Small, focused commits; no issue-tracker IDs in code (branch names, commit
  messages, and PR descriptions may reference them).
- Keep the **PR description current** as the branch evolves (features pulled
  forward, review hardening, provider switches) — a stale PR body is a
  factual error.
- Close the loop on the ticket: check the DoD, add a delivery summary, link
  the PR.
- **Phase B backlog**: file the deferred features with a *self-contained*
  context document (architecture recap, verified SDK facts, invariants any
  change must preserve) — assume the plan file will not survive in the repo.

## Debugging philosophy

- **Reduce complexity first.** When a bug appears, simplify the offending
  function before adding conditionals.
- **Never patch-and-bolt-on.** Fix the root cause or change the design, even
  if the patch looks smaller.
- **Repeated churn in one area = wrong direction.** If the same seam needs a
  second or third fix, stop, write up what keeps breaking and the design
  alternatives, and escalate to the human. Better to re-decide direction
  than accrete fixes.

## Definition of Done (ship checklist)

- [ ] Plan doc written from the Half-1 template; five-question gate passed
- [ ] Installed-package inspection done; every doc claim verified or corrected
- [ ] Converter + session manager + adapter implemented, registered
      everywhere (`__init__`s, pyproject extra + dev, conformance, drift green)
- [ ] Unit tests green with fakes only at the client seam; isolation, resume,
      eviction, rollback, and ownership semantics all covered
- [ ] Quality bar held: Adapter Constructor Shape config, shared constants, no closures smuggling
      state, ownership explicit, PEP 257, WHY-only comments
- [ ] `/code-review` (high) run; findings fixed at root cause; external review
      claims validated before acting
- [ ] Docs/comments fact-check audit passed
- [ ] Live probes green (happy path + failure path + multi-turn where
      behavior spans turns); live E2E green or credential-gated with
      fail-loud wiring
- [ ] ruff / pyrefly / full unit suite green; CI green on the PR
- [ ] PR description and ticket current; deferred work filed with a
      self-contained context document
