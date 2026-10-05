# ACP (Agent Client Protocol) Integration

Facts about the ACP integration that span modules or that the code cannot say for
itself. The client side is `ACPClientAdapter` (Band room → room-owned ACP subprocess);
the server side is `ACPServer` + `BandACPServerAdapter`.

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

## Band tools

- **Injected tools are bound to the room.** With `inject_band_tools` on (the
  default), the adapter hosts one loopback `LocalMCPServer` and gives each room's
  session that room's endpoint (`/rooms/<room>/mcp`, or `/sse`), so the tools take no
  `chat_id` and the prompt never states one. A reloaded session gets the same endpoint.
  If that server dies, the next message replaces it on a new port, and a room whose
  session still dials the old one gets a fresh session with the transcript replayed.
- **An external Band MCP server takes the room as an argument.** With
  `inject_band_tools=False` (a remote `band-mcp`), the session's first prompt states
  `Current chat_id` for its tools to use.

## Turn delivery

- **Narration is live and ordered.** `ACPCollectingClient` streams finalized chunks to
  `RoomTurnEmitter` as they arrive, so a Band tool's own room post (a remote band-mcp
  posts over REST mid-turn) lands between its `tool_call` and `tool_result`.
- **Assistant text is held to turn close** and relayed as one reply through `relay_reply`,
  unless the turn already replied or declined. Injected Band tools record their own effect
  on `tools.turn`. With `inject_band_tools=False` an external band-mcp runs out of process,
  so the emitter records each completed call's `turn_effect` from the `tool_call` title
  instead, and only in that mode, so no call is counted twice. Narrated names are
  canonicalized (`canonicalize_mcp_tool_name`) so Copilot's `band-` prefix never reaches
  the room.
- **`emit=` never gates** that recording, the held text, or the closing `task` event.
  That event is resume state:
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
  session; Cursor turns are serialized. Decision prompts, timeout notices and `/cursor`
  replies post through `send_notice`, so they never count as the model's reply, and a
  `/cursor` message settles its own turn without reaching the agent. A turn parked on a
  decision releases its message early (`tools.turn.detach()`), so it is judged when the
  ACP turn completes normally; a failed or cancelled turn is never reported as a missing
  reply.
  The adapter does not pick a plan/agent mode itself; a caller selects one through
  `resolve_session_config`, which reads each session's advertised catalog before the
  first prompt. The live `backends` lane pins the Cursor CLI and passes `E2E_CURSOR_API_KEY`
  as `CURSOR_API_KEY` only to its baseline step; local runs may use a stored `agent login`.

## Server prompt outcomes

`ACPServer.prompt` settles on the first terminal outcome for its room:

- Completed text returns `end_turn`; `session/cancel` returns `cancelled`. A Band `error`
  rejects the prompt with JSON-RPC `internal_error`, the Core failure projection in
  `error.data`; room cleanup and agent shutdown also fail it. A second prompt while one is
  pending in that room is rejected with `invalid_params`.
- Room events bind to a prompt only once its Band post returns, so a previous turn's
  late event stays unsolicited and cannot settle the prompt or drop the send. A terminal
  outcome releases the prompt even while its Band REST send is still pending. One
  timeout bounds the send and the reply together.
- Error updates carry the same projection on the agent-message chunk's `_meta`, unsolicited
  errors in mapped rooms included. Their text and projected keys and values are
  credential-redacted; values under credential-named fields are redacted in full.
- The ACP Python client drains queued update handlers before surfacing a prompt error, so
  a slow client handler can still delay its own `prompt()`.
