# Thenvoi SDK Parity Roadmap

Status: proposed · Scope: `thenvoi-sdk-typescript` + `thenvoi-sdk-python`

This roadmap defines the **order of work** (not a timeline) required to bring the
TypeScript and Python SDKs to functional parity. The two SDKs share an
intentionally identical architecture (Agent → PlatformRuntime → per-room
execution → ThenvoiLink transport; the same `thenvoi_*` tool set, contact
strategies, MCP backends, A2A/ACP bridges). The gaps below are the deltas that
remain.

Each item links to a requirement doc. Requirement IDs are global and stable
across both repos:

- `REQ-TS-*` — work that lands in the **TypeScript** SDK.
- `REQ-PY-*` — work that lands in the **Python** SDK.

The full requirement docs live in `docs/parity/` of the repo that owns the work.
This roadmap is duplicated in both repos and kept identical.

## Gap inventory

### TypeScript SDK is missing (vs Python)

| ID | Item | Priority | Effort |
|----|------|:--------:|:------:|
| REQ-TS-01 | `AdapterFeatures` capability-gating system | P0 (structural) | M |
| REQ-TS-02 | Pydantic AI adapter | P1 | M |
| REQ-TS-03 | CrewAI adapter | P1 | L |
| REQ-TS-04 | CrewAI Flow adapter | P1 (depends on TS-03) | L |

### Python SDK is missing (vs TypeScript)

| ID | Item | Priority | Effort |
|----|------|:--------:|:------:|
| REQ-PY-01 | OpenAI direct adapter | P0 | S |
| REQ-PY-02 | Generic / callback adapter | P0 | S |
| REQ-PY-03 | Linear bridge integration | P1 | L |
| REQ-PY-04 | Shared tool-calling base refactor | P2 (alignment) | M |

### Intentional divergences (decide before scheduling)

These are not straightforward ports; they need an explicit "port vs. skip"
decision rather than being treated as tickets.

| Item | Side missing | Recommendation |
|------|--------------|----------------|
| Vercel AI SDK adapter | Python | Likely permanent JS-only divergence (the `ai` package is JS-only). Skip unless a Python analog is desired. |
| OpenClaw channel plugin | Python | In scope only if OpenClaw distribution is a Python goal. Default: skip. |

## Order of work

The ordering optimizes for: (1) lay structural foundations before building on
them, (2) bank cheap wins early, (3) push hard/uncertain items later, (4) leave
pure-alignment refactors last.

### Phase 0 — Foundation & quick wins

1. **REQ-PY-01 — OpenAI direct adapter (Python).** Cheapest capability gap; the
   tool-calling loop already exists in the Anthropic/Gemini adapters and can be
   reused directly.
2. **REQ-PY-02 — Generic/callback adapter (Python).** Small, high-DX-value
   addition mirroring the TS `GenericAdapter`.
3. **REQ-TS-01 — `AdapterFeatures` capability system (TypeScript).** Structural.
   Do this before porting more adapters so new adapters are built on the correct
   capability/feature abstraction rather than ad-hoc booleans.

### Phase 1 — Major capabilities

4. **REQ-PY-03 — Linear bridge (Python).** The one large capability Python lacks;
   self-contained, clear scope.
5. **REQ-TS-02 — Pydantic AI adapter (TypeScript).** Build on the existing
   `ToolCallingAdapter` base; benefits from REQ-TS-01 being in place.
6. **REQ-TS-03 — CrewAI adapter (TypeScript).** Hard — no first-class JS CrewAI
   exists. Requires a design decision (see the requirement doc) before build.
7. **REQ-TS-04 — CrewAI Flow adapter (TypeScript).** Depends on REQ-TS-03.

### Phase 2 — Alignment (optional)

8. **REQ-PY-04 — Shared tool-calling base refactor (Python).** Not a feature gap;
   aligns Python's per-adapter loops with the TS `ToolCallingAdapter` factoring to
   ease future cross-porting.

### Decision gate (no work until resolved)

- Vercel AI SDK adapter for Python — port or skip?
- OpenClaw channel plugin for Python — in scope or skip?

## Definition of "at parity"

Parity is reached when:

- Both SDKs expose the same adapter catalog **except** for documented intentional
  divergences (Vercel AI SDK, OpenClaw, and any Python-ecosystem-only frameworks
  the decision gate rules out of TS scope).
- Both SDKs expose an equivalent capability/feature-gating model (REQ-TS-01).
- Both SDKs ship the Linear bridge (REQ-PY-03) or it is explicitly de-scoped.
- The fast path: completing Phase 0 + REQ-PY-03 makes the SDKs "feel at parity";
  the CrewAI/Pydantic AI items on the TS side are the only genuinely hard
  remainders and are gated on the design decisions in their requirement docs.
