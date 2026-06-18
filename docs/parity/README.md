# Thenvoi SDK Parity Docs (Python)

This folder tracks the work to bring the Python and TypeScript Thenvoi SDKs to
functional parity. See [`ROADMAP.md`](./ROADMAP.md) for the order of work across
both repos (no timeline).

The requirement docs in this folder cover work that lands in **this** (Python)
repo. TypeScript-side requirement docs live in the `thenvoi-sdk-typescript` repo
under the same `docs/parity/` path. Requirement IDs are global and stable across
both repos.

## Requirements owned by the Python SDK

| ID | Title | Priority |
|----|-------|:--------:|
| [REQ-PY-01](./REQ-PY-01-openai-adapter.md) | OpenAI direct adapter | P0 |
| [REQ-PY-02](./REQ-PY-02-generic-adapter.md) | Generic / callback adapter | P0 |
| [REQ-PY-03](./REQ-PY-03-linear-bridge.md) | Linear bridge integration | P1 |
| [REQ-PY-04](./REQ-PY-04-tool-calling-base-refactor.md) | Shared tool-calling base refactor | P2 |

## Context

The two SDKs share an identical architecture by design (the TypeScript SDK is a
deliberate port of this Python reference implementation). As of this writing:

- Python SDK: v0.2.11
- TypeScript SDK: v0.1.6

The gaps below are the remaining deltas. See the roadmap for the TypeScript-side
gaps (`REQ-TS-*`) and the intentional divergences.
