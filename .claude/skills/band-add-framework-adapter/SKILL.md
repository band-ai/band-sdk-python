---
name: band-add-framework-adapter
description: Use this skill when the user asks to add a new framework adapter to the Band Python SDK — "add an adapter for framework X", "integrate SDK Y as an adapter", "implement the Z adapter from this ticket". Takes a ticket + context and drives the full process — parallel research (repo patterns + online SDK source), installed-package ground-truthing as a hard gate, a fillable plan template, a five-question plan gate, TDD implementation, the concrete quality bar (config per the Adapter Constructor Shape, shared constants, no bolt-ons), scrutinized review rounds, live runtime probes, live E2E, and ship gates.
---

# Add a Framework Adapter

Read [the shared workflow runtime](../workflow-runtime.md) before execution.
Use its host-native tools, bounded delegation, sequential fallback, and cleanup
rules for every role and review pass below.

Turn a framework request into a verified adapter, meaningful tests, and live
evidence. Match the SDK family before choosing its architecture.

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

## Plan template

Before writing code, read [plan-template.md](plan-template.md) and fill it from
verified research. It covers capability mapping, state ownership, tool
collisions, rehydration, and deferred scope. Apply only architecture that the
framework actually needs.

## Team & roles

Run the work as roles. An orchestrating session may map them to subagents;
a single implementing agent treats them as **review passes to run**, not
subagents to spawn.

| Role | Responsibility | Mapped to |
|---|---|---|
| Project manager | Sequences phases, holds scope (core only), tracks tasks | main session + task tracking |
| Architect | Guards state ownership and room isolation | Architecture pass at phase boundaries |
| Developer(s) | Implements slices (converter, adapter, tests, example, manager if needed) | Implementing session; delegate independent slices only with exclusive file ownership |
| Principal | Guards simplicity, typing, naming, comment discipline | Local diff review using the project review angles |
| Peer review | Verifies diff logic (bridging, resume/fallback, isolation) | Independent review when available; otherwise a reported self-review |
| QA | Runs suites, triages failures, owns flake policy | `/band-write-e2e-test`, `/band-investigate-test-error`, live runs |

Cadence: plan a slice → implement → review → test → next. Keep slices small
so review stays cheap.

## Phase 1 — Research (parallel, before any plan)

Run these independent research passes within available capacity:

1. **Repo pattern maps** (read-only research roles): the closest template adapters
   (which family? subprocess-runtime adapters mirror claude_sdk/codex;
   direct-API mirror anthropic/gemini), the conformance registries, and a
   **test-infrastructure reality check**. Do not trust docs, memories, or
   design notes about test infra — read the actual branch. Registration steps
   and helpers named in older notes are often renamed or gone.
2. **SDK ground truth online**: read the SDK's *source* (README plus the
   actual `client.py`/`session.py`/`tools.py` files), not just docs pages.
   Docs lie by omission and by staleness; note every discrepancy.

Then the **hard gate — step 0 of implementation**: add the pyproject extra,
update the lockfile intentionally with `uv lock`, run
`uv sync --locked --all-packages --extra <matching-dev-extra>`, and inspect
the installed package with `inspect.signature(...)` on
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

Fill the linked plan template into a plan file. Before coding, answer five
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
3. **Adapter + state owner, if needed.** Reuse the runtime's existing turn
   serialization. Where native sessions need a manager, give it lifecycle,
   room mapping, and cleanup ownership; any additional locks must retain
   identity while waiters exist. Verify the SDK's abort/resume contract
   before choosing recovery or eviction. The adapter owns tool bridging,
   prompt composition, and event emission. Do not cache liveness the client
   can answer itself.
4. **Adapter unit tests** with a realistic fake at the verified external
   boundary (for a client/session runtime, see `FakeCopilotClient` in
   `tests/adapters/copilot_sdk/fakes.py`) and the real
   `FakeAgentTools`. Never mock the thing under test. Cover: reply+mention;
   system-message assembly; bridged tool execution + events; custom-tool
   precedence; validation-error mapping;
   **two rooms without leakage**; cleanup and failure rollback. For native
   session runtimes, also cover resume, resume-miss history, and recovery.
   Apply [test conventions](../band-python-test-conventions/SKILL.md), including
   "Test helpers and peers". Exercise real components for protocol, callback
   context, and lifecycle claims; do not reproduce those mechanisms in fakes.
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

- **Constructor shape**: exactly as `AGENTS.md` "Adapter Constructor Shape"
  describes. In addition, a routing policy is one argument (`None | "mode" |
  handler`), not parallel booleans/callbacks that can contradict each other.
- **No magic strings**: use `MessageType` (StrEnum) for event types; shared
  constants for cross-adapter facts (`ToolEventKey` in `core/types.py`);
  single-source metadata keys shared between adapter (writer) and converter
  (reader). Model-facing contract text (sentinel
  tool results, injected prompt guidance) is design surface, not string
  filler: shared constants, unit-tested, and taught in the system prompt —
  the model should not first learn a contract from its first tool result.
- **Explicit resource ownership**: injected client = borrowed (never stopped
  by the adapter); factory/default = owned (stopped at `cleanup_all`).
  Mutually-exclusive inputs raise `BandConfigError`.
- **Lifecycle discipline**: initialize owned resources in `on_started` with
  rollback; `cleanup_all` releases them. Probe auth where the SDK supports
  a suitable check. For failed/timed-out turns, follow the verified SDK
  cancellation and recovery contract; never leave work running unnoticed.
- `%s` logging with room/session ids traceable per lifecycle.
- The `band-python-conventions` skill applies
  throughout.

## Phase 5 — Review rounds

1. Review the diff locally using
   [the project review angles](../band-code-review/angle-groups.md) and an
   available `simplify` skill; fix findings at the
   **root cause** — a fix that adds a branch to a tangled function is a
   smell; extract and clarify instead. Findings tend to cluster; a cluster in
   one area means redesign that area once, not N patches. This local review
   does not post to GitHub. Run `band-code-review` only when explicitly
   requested and an open PR exists.
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
   with fallback suppression, history recall (native resume where supported), and at least one
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

- If publishing a PR is authorized, create it as a draft. Before promotion,
  perform the author readiness review in `AGENTS.md` "PR Lifecycle and
  Readiness Review", using the plan, final diff, scenario coverage, and
  applicable validation evidence. Early draft feedback is welcome.
- `AGENTS.md` "Pre-Commit Checklist" (ruff, pyrefly and the full unit suite).
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

- [ ] Plan doc written from the linked template; five-question gate passed
- [ ] Installed-package inspection done; every doc claim verified or corrected
- [ ] Converter + adapter implemented, registered
      everywhere (`__init__`s, pyproject extra + dev, conformance, drift green);
      add a session manager only where the verified state model needs one
- [ ] Unit tests green with fakes only at the client seam; isolation, history,
      rollback, ownership, and supported native-session recovery covered
- [ ] Quality bar held: Adapter Constructor Shape config, shared constants, no closures smuggling
      state, ownership explicit, PEP 257, WHY-only comments
- [ ] Local diff review complete; findings fixed at root cause; external review
      claims validated before acting
- [ ] Docs/comments fact-check audit passed
- [ ] Live probes green (happy path + failure path + multi-turn where
      behavior spans turns); live E2E green or credential-gated with
      fail-loud wiring
- [ ] ruff / pyrefly / full unit suite green; CI green on the PR
- [ ] PR description and ticket current; deferred work filed with a
      self-contained context document
