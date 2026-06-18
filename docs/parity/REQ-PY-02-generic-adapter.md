# REQ-PY-02 — Generic / callback adapter

- **Owner SDK:** Python
- **Priority:** P0
- **Effort:** Small
- **Status:** Proposed
- **Parity reference:** TypeScript
  `packages/sdk/src/adapters/GenericAdapter.ts` (`GenericAdapter`,
  `GenericAdapterHandler`)

## Problem

The TypeScript SDK ships a `GenericAdapter` — a `SimpleAdapter` that takes a
single async callback and lets a developer implement an agent's logic in one
function without subclassing:

```ts
new GenericAdapter(async ({ message, tools }) => {
  await tools.sendMessage(`Echo: ${message.content}`);
});
```

The Python SDK has **no equivalent**. Today a Python developer must subclass
`SimpleAdapter` even for trivial logic. This is a small, high-DX-value gap.

## Goal

Add a `GenericAdapter` to the Python SDK that wraps a single async callback.

## Requirements

### Functional

1. Add `GenericAdapter(SimpleAdapter[HistoryProvider])` (location:
   `src/thenvoi/adapters/generic.py` or alongside the base) that accepts a
   handler callable in its constructor.
2. The handler signature should provide the same context the TS handler gets,
   adapted to Python naming:
   `async def handler(*, message, tools, history, participants_msg,
   contacts_msg, is_session_bootstrap, room_id, agent_name, agent_description)`.
   Prefer a typed `Protocol`/`Callable` alias (e.g. `GenericAdapterHandler`).
3. `on_message(...)` simply invokes the handler with the provided arguments.
4. Export it from the adapters package surface so it is importable as
   `from thenvoi.adapters import GenericAdapter` (and re-export at the top level
   if sibling adapters are).

### Acceptance criteria

- Constructing `GenericAdapter(handler)` and running an agent dispatches messages
  to `handler` with the documented kwargs.
- Unit test in `tests/adapters/test_generic_adapter.py` verifies the handler is
  called with correct arguments and that `tools.send_message` round-trips.
- An `examples/basic/` (or `examples/generic/`) echo script demonstrates it, with
  PEP 723 metadata per CLAUDE.md.
- `ruff` / `pyrefly` clean.

## Affected code (Python)

- `src/thenvoi/adapters/generic.py` (new) + adapters `__init__` export
- `tests/adapters/test_generic_adapter.py` (new)
- `examples/` (new or updated echo example)

## Dependencies

None.

## Out of scope

- Any LLM integration — this adapter is intentionally logic-only.
