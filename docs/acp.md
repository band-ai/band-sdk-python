# ACP (Agent Client Protocol) Integration

Facts about `ACPClientAdapter` (Band room → room-owned ACP subprocess) that span
modules or that the code cannot say for itself. The server side is
`ACPServer` + `BandACPServerAdapter`.

## Configuration

`ACPClientAdapterConfig` holds the plain settings; `CursorACPAdapterConfig`,
`CopilotACPAdapterConfig` and `OmpACPAdapterConfig` extend it with backend defaults
and fields. Callables (`workspace_for_room`, `resolve_session_config`,
`resolve_permission`) are keyword-only constructor arguments.

```python
from band.adapters import ACPClientAdapter, ACPClientAdapterConfig

config = ACPClientAdapterConfig.model_validate(
    {"command": "codex-acp", "turn_timeout_s": 600}
)
adapter = ACPClientAdapter(config)
assert adapter.config.command == ("codex-acp",)
```

## Turn delivery

- **Narration is live and ordered.** `ACPCollectingClient` streams finalized chunks to
  `RoomTurnEmitter` as they arrive, so a Band tool's own room post (a remote band-mcp
  posts over REST mid-turn) lands between its `tool_call` and `tool_result`.
- **Assistant text is held to turn close** and relayed only if no completed call settled
  the reply (`turn_replied_in_room`, `settles_turn_reply`). Detection matches the
  `tool_call` title, because tools may run out-of-process. Narrated names are canonicalized
  (`canonicalize_mcp_tool_name`) so Copilot's `band-` prefix never reaches the room.
- **`emit=` never gates** chunk recording (the reply decision must see the whole turn),
  the held text, or the closing `task` event. That event is resume state:
  `ACPClientHistoryConverter` reads `acp_client_session_id` / `acp_client_room_id` from it
  to `session/load` after a restart.
- **Approved permissions are silent.** Only a denied request posts a synthetic
  `tool_call`/`tool_result` pair, and only when `Emit.TOOL_CALLS` is on.
- **Replay happens once**, only for a freshly minted session (a failed `session/load`
  counts), under a nonce'd boundary marker so a replayed message cannot spoof it. History
  stops strictly before the triggering message (`messages_before`).

## Isolation

- The per-room workspace (`./.band-workspaces/<room-id>`) is not an OS sandbox; configure
  the agent's own sandbox when that boundary matters.
- TCP and custom transports are rejected: one remote process cannot be proven to serve
  exactly one room.

## Session config

`resolve_session_config` runs after each new or restored session and before its first
prompt. Each successful `session/set_config_option` response replaces the catalog the next
selection is validated against (choosing a model can change the reasoning levels). Any
failure fails that room turn visibly instead of falling back.

## Backends

- **OMP:** `approval_mode="yolo"` bypasses OMP's native approval and gives the agent full
  access to its host. It does not change Band tool registration or platform permissions.
  `OmpACPAdapterConfig.model` is passed as OMP's `--model` flag; OMP does not read an
  `OMP_MODEL` env variable. Set `api_key` with it and the adapter passes the key in the env
  variable that model's provider needs.
- **Cursor:** question, plan and permission decisions default to `manual`, resolved by a
  room participant with `/cursor <word> <token>`. Cursor omits the session id on its
  extension notifications, so the adapter holds a turn lock and binds them to that turn's
  session; Cursor turns are serialized.
