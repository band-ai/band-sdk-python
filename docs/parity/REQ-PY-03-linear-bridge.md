# REQ-PY-03 — Linear bridge integration

- **Owner SDK:** Python
- **Priority:** P1
- **Effort:** Large
- **Status:** Proposed
- **Parity reference:** TypeScript `packages/sdk/src/integrations/linear/`,
  `packages/sdk/src/linear/`, exported at `@thenvoi/sdk/linear`; example at
  `examples/linear-thenvoi/`

## Problem

The TypeScript SDK ships a full **Linear bridge**: it connects Thenvoi agent
rooms to Linear issues / agent-sessions, handles Linear webhooks, persists
room↔session mappings, and exposes Linear activity tools to the agent. The Python
SDK has **no Linear integration at all**. This is the single largest capability
the Python SDK is missing relative to TypeScript.

## Goal

Add an equivalent `thenvoi.integrations.linear` package (importable from a clear
public surface) providing the Linear↔Thenvoi bridge.

## Scope (mirrors the TypeScript surface)

### Functional

1. **Bridge config** equivalent to `LinearThenvoiBridgeConfig`:
   `linear_access_token`, `linear_webhook_secret`, `room_strategy`
   (`issue` | `session`), `writeback_mode` (`final_only` | `activity_stream`),
   `host_agent_handle`, `planning_agent_handles`, `implementation_agent_handles`.
2. **Session/room store** equivalent to `SessionRoomStore` +
   `SessionRoomRecord`, with a SQLite-backed implementation
   (`create_sqlite_session_room_store` equivalent). Methods: get by session id,
   get by issue id, upsert, mark canceled, enqueue/list/mark bootstrap requests,
   close.
3. **Runtime + webhook handling** equivalent to `createLinearBridgeRuntime`,
   `handleAgentSessionEvent`, `completeLinearSession`: verify the webhook
   signature, route Linear agent-session events to rooms, and write agent output
   back to Linear per `writeback_mode`.
4. **Linear activity tools** equivalent to `createLinearTools`: custom tools for
   `linear_post_thought`, `linear_post_action`, `linear_post_error`,
   `linear_post_response`, `linear_post_elicitation`, `linear_update_plan`,
   returned as `CustomToolDef`s.
5. **Optional dependency extra** in `pyproject.toml` (e.g. `linear`) wrapping the
   Linear SDK / API client and the webhook server deps (reuse the existing
   `starlette`/`uvicorn` approach used by the a2a_gateway extra where possible).
6. **Example** at `examples/linear-thenvoi/` (a bridge server) with PEP 723
   metadata per CLAUDE.md.

### Acceptance criteria

- A bridge server can be started, receive a (signature-verified) Linear webhook,
  create/lookup the mapped Thenvoi room, and write agent activity back to Linear.
- The SQLite store round-trips records and the bootstrap-request queue.
- The Linear activity tools post to the correct Linear session and are gated by
  the agent's feature/capability config.
- Unit tests cover webhook verification, store CRUD, room-strategy selection,
  writeback modes, and the activity tools. Live API calls are isolated to
  `tests/integration/` (skipped in CI).
- `ruff` / `pyrefly` clean.

## Affected code (Python)

- `src/thenvoi/integrations/linear/` (new package: config, store, runtime,
  webhook handler, tools)
- Public re-export surface for the bridge + tools
- `pyproject.toml` (new `linear` extra)
- `examples/linear-thenvoi/` (new)
- tests under `tests/` (+ `tests/integration/`)

## Dependencies

- Reuses `CustomToolDef` and the existing webhook-server dependency pattern.
- Independent of the adapter-side gaps; can proceed in parallel.

## Out of scope

- The OpenClaw channel plugin (separate intentional-divergence decision).
- Any change to the Thenvoi platform-side Linear support.
