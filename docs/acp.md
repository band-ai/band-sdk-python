# ACP (Agent Client Protocol) Integration

Facts about the ACP integration that span modules or that the code cannot say for
itself. The client side is `ACPClientAdapter` (Band room → room-owned ACP subprocess);
the server side is `ACPServer` + `BandACPServerAdapter`.

Client-side automatic idle release is unavailable pending provider-specific
safety qualification; see [idle room resource retention](idle-room-release.md).

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
- **Native summaries finish the provider turn.** After the required Band action,
  the prompt requests a brief tool-free summary so coding runtimes do not keep
  continuing. That summary is telemetry, never another room reply.
- **Held agent text is thought telemetry.** At clean prompt close, successful
  external Band-tool effects are recorded first; held native text is an optional
  thought without mentions, suppressed after a successful reply or decline.
  It never settles a turn. Failed tools alone and native-only output reach the
  shared missing-reply verdict, with thoughts enabled or disabled.
- **Injected Band tools record their own successful effects.** With
  `inject_band_tools=False`, completed external tool effects are staged from
  normalized identities until successful close. Failed calls record nothing.
  Such peers need external Band tools to reply or decline; native text alone
  no longer supplies room replies.
- **`emit=` never gates effects or resume state.** The closing session `task`
  carries restoration identifiers after held-text handling, regardless of flags.
  Runtime judging may report an error afterward. Failed/cancelled prompts post
  neither held text nor resume state.
- **Migration:** delete retired `assistant_text_mode` config keys; explicit
  configs reject them. Tool filters narrow SDK-injected registrations; external
  MCP servers remain outside SDK registration control.
- **Approved permissions are silent.** Only a denied request posts a synthetic
  `tool_call`/`tool_result` pair, and only when `Emit.TOOL_CALLS` is on.
- **Definitively rejected prompts stay pending.** The exact OMP JSON-RPC busy
  response (`-32003` with `data.reason == "session_busy"`) raises
  `InputNotAccepted` after one attempt. It preserves the room's runtime and
  session without cancellation or a failure event; the shared runtime schedules
  later attempts. Other errors retain normal failure cleanup. Session-scoped
  external notifications cannot complete the rejected request: their successful
  effects commit only after a prompt closes successfully.
- **Bootstrap commits after successful prompt completion.** A rejection keeps
  room context owed. Fresh sessions also keep transcript replay owed; restored
  sessions already hold their history and receive only Band context. Replay uses
  a nonce'd boundary marker so history cannot spoof the live message, and stops
  strictly before the triggering message (`messages_before`).

## Upgrading from SDK 4.x to 5.x

ACP client adapters and Codex no longer relay native assistant text as room
messages. Replies must use `band_send_message`; messages needing no response
must use `band_no_reply`. Native text is optional thought telemetry and cannot
hide a missing reply. The shared turn judgment and other adapters' text relays
are unchanged.

- Keep SDK-injected Band tools enabled, or supply a reachable external Band MCP
  server when using `inject_band_tools=False`. A conversation-only ACP peer
  without Band tools can no longer reply or deliberately decline. The
  [Copilot sandbox example](../examples/acp/copilot_sandbox/README.md) requires
  `BAND_MCP_SSE_URL` for that reason.
- Update custom prompts to use the two Band tools. Coding runtimes may finish
  with a native summary after the required action; that summary is not another
  room message.
- Remove Codex's `fallback_send_agent_text` config key and
  `CODEX_FALLBACK_SEND_AGENT_TEXT` environment variable. Also remove
  `assistant_text_mode` from any configurations using the retired workaround
  and `CODEX_ASSISTANT_TEXT_MODE` from the environment. Explicit config keys
  are rejected; retired environment variables are ignored.
- Keep the required communication tools available when changing tool filters.
  SDK-injected ACP registrations now honor those filters. Start a fresh Codex
  thread after changing filters; resumed threads retain their saved tools.

These are breaking changes; integrations relying on native-text relay must
migrate before upgrading. TypeScript parity is a separate change.

## Isolation

- The per-room workspace (`./.band-workspaces/<room-id>`) is not an OS sandbox; configure
  the agent's own sandbox when that boundary matters.
- TCP and custom transports are rejected: one remote process cannot be proven to serve
  exactly one room.

## Room ownership

Each room is one `ACPRoom`: its process, connection and session, composed with a
`RetainedRoom` from the adapter's single `RoomRetention`. Detached work (a Cursor
turn, a Copilot model switch) is owned by the room before it runs. A prompt binds
its tools under the room's lease, and a model switch takes the room's reservation
in turn with its prompts, so an independent room never waits on another room's
start, prompt, switch or exit. The room's acquisition starts its process within
`retention.transition_timeout_s`, which defaults to `ACP_TRANSITION_TIMEOUT_S` for
every ACP profile. A start that outlasts it fails that turn with
`PROCESS_NOT_READY_ERROR` and is recovered; raise that one bound for a slow agent.
A turn that stops waiting for a start, at its bound or on `/stop`, cancels it, so
the room's next message gets a new process. A leave or stop closes admission at
once and gives the room's running turn and model switch `turn_settle_timeout_s`
(`TURN_SETTLE_GRACE_S` by default) to finish on their own before cancelling them.

```python
from band.core.retention import RoomRetentionConfig
from band.core.turn_lifecycle import TURN_SETTLE_GRACE_S
from band.integrations.acp.client_adapter import (
    ACP_TRANSITION_TIMEOUT_S,
    ACPClientAdapterConfig,
)

config = ACPClientAdapterConfig(command="my-agent")
assert config.retention.transition_timeout_s == ACP_TRANSITION_TIMEOUT_S
assert config.turn_settle_timeout_s == TURN_SETTLE_GRACE_S

slow = ACPClientAdapterConfig(
    command="my-agent", retention=RoomRetentionConfig(transition_timeout_s=120)
)
assert slow.retention.transition_timeout_s == 120
```

- **A deliberate wake reloads the exact session.** It calls `session/load` with a
  fresh Band MCP endpoint, then reapplies the room's model selection and verifies
  the result. A load or selection refusal fails the wake and keeps the session id and
  selection; it never creates a replacement session. Idle release is off, so no
  wake happens yet; this is the path a qualified provider will take.
- **A failed turn recovers its room.** A session-setup or prompt error, or a
  timeout, releases that room's process for its generation; the next turn starts a
  new process and reloads the same session. If the agent no longer has it, the turn
  falls back to a fresh session with a transcript replay. A refused remembered
  selection fails that turn only and reverts to the configured one; the room keeps
  its session, and when that refusal follows a reload, the room's process is
  recovered too, so the next turn reloads the session on a new process rather
  than twice on the same one. Only a bootstrap resume candidate is closed when its configuration
  is refused.
- **Closure is confirmed, not assumed.** The process handle stays on the room until
  `__aexit__` succeeds and the agent's stdout reaches EOF, so a descendant that
  inherited it keeps the room closing until it exits, with a warning naming the
  agent's pid. A failed or cancelled exit leaves the room owned, and a retry kills
  and waits for the same process. `cleanup_all()` and its alias `stop()` share one
  `transition_timeout_s` budget across the rooms and the shared Band MCP server,
  and keep that server open while any room is still held. A cancelled, failed or
  overdue server close stays owned and a retry joins it. A stop is final: later
  turns are refused until `on_started` (an `Agent.start()`) finishes it and serves
  again.
- **Idle release stays off.** OMP, Cursor and Copilot report `ProviderActivity.UNKNOWN`,
  so an idle release always answers BUSY. Native qualification is still owed: OMP
  background jobs (including suppressed or acknowledged work), Cursor work beyond
  subagent drain, and Copilot scheduled `/after` prompts. Band MCP calls and ACP
  permission or extension requests carry no prompt id, so a call arriving after its
  turn is served with the room's current tools.

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
  room participant with `/cursor <word> <token>`. Band's own tools, including
  `additional_tools`, are approved once per call in every `approval_mode` during
  an active turn. If Cursor offers no allow-once option, the configured approval
  policy applies instead. Late Band permission requests are refused after the
  turn closes or is interrupted. Cursor omits the session id on its
  extension requests, so each room process gets its own client profile and binds them to
  that room's active turn on that process; a request from a replaced process opens no
  decision. Rooms run in parallel, one turn per room; a message that arrives while its
  room's turn still runs is not accepted, so the host keeps it unacknowledged and
  retries it once the room is free, and a `/cursor` reply still reaches the parked
  turn. Only the turn holding the room's lease answers Cursor's extension requests,
  and a failed turn reports its failure before the lease passes on, once, whether
  it failed starting or prompting. An interrupt or `/stop` cancels the room's
  accepted turn, whether it is starting, prompting or parked. `max_pending_decisions` caps each room
  separately. Decision prompts, timeout notices and `/cursor` replies post through
  `send_notice`, so they never count as the model's reply, and a
  `/cursor` message settles its own turn without reaching the agent. A turn parked on a
  decision releases its message early (`tools.turn.detach()`), so it is judged when the
  ACP turn ends: a failed turn reports its own failure, an interrupt reports nothing,
  and a turn its room's leave or stop cancels after the settle grace is reported once
  as a missing reply.
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
  outcome settles the prompt even while its Band REST send is still pending; the
  request then returns once the cancelled send and completion timer have unwound. One
  timeout bounds the send and the reply together.
- Error updates carry the same projection on the agent-message chunk's `_meta`, unsolicited
  errors in mapped rooms included. Their text and projected keys and values are
  credential-redacted; values under credential-named fields are redacted in full.
- The ACP Python client drains queued update handlers before surfacing a prompt error, so
  a slow client handler can still delay its own `prompt()`.

## Copilot host model catalog

`await copilot_adapter.probe_models(timeout_s=60.0)` returns the canonical
`ModelCatalog` for the configured Copilot account without a Band connection,
room, room workspace, ACP session or model turn. Install the optional
`band-sdk[acp,copilot_sdk]` dependencies to use it; ordinary ACP construction and
room operation do not require the Copilot SDK.

Model probing requires a released `github-copilot-sdk>=1.0.5,<2`. The probe
rejects an unsupported version or subprocess-field layout before starting
the CLI.

The probe starts the configured Copilot executable through the SDK's stdio
protocol, removing only the ACP-specific `--acp` argument. It retains other
configured arguments and uses `cwd` itself, not a fabricated room directory.
Executable lookup honors the configured child `PATH` and working directory,
including configured `PATHEXT` rules on Windows for both bare and path-qualified
commands. Windows lookup confirms an existing file before SDK startup. Every
searched candidate is absolute; the host's current directory is not implicitly
searched. The probe does not choose the SDK's bundled executable or fall back to
an unrelated host executable when the configured search path has no match.

For a Windows JavaScript entrypoint, the probe resolves `node.exe` through the
configured `PATH` and working directory, then supplies its absolute path and the
script argument through the public SDK stdio connection. This preserves the SDK's
native Node interpreter choice without letting a bare `node` search the host
environment. A missing configured interpreter fails before starting a child.

The child receives ACP's trimmed inherited environment plus the backend and
configured environment. Put token variables in `config.env`, where
`COPILOT_GITHUB_TOKEN` takes precedence over `GH_TOKEN`, then `GITHUB_TOKEN`.
`config.github_token` supplies the lowest-priority `GITHUB_TOKEN` convenience
value; an explicit `config.env["GITHUB_TOKEN"]` overrides it. Unrelated token
variables exported only in the host process are not implicitly injected.
Stored CLI login remains available through the inherited home environment.

This lists hosted-account models, not BYOK provider catalogs. It does not prove
inference entitlement or validate configured model/effort selection, MCP servers
or tools. Advertised reasoning efforts remain `None` when unknown and `()`
when explicitly absent; the probe invents neither efforts nor provider, default
or currently selected model metadata. A successful empty catalog is distinct
from a missing SDK, unsupported operation or failed discovery. This explicit
host API does not replace the startup `list_models` hook or run automatically
when a room starts.

Ordinary discovery failures raise a safe `BandConfigError`; cancellation
propagates. `timeout_s` bounds discovery reporting and, separately, cleanup
reporting. `CleanupIncompleteError` means the adapter still owns the client,
physical child or late startup: retry `await adapter.cleanup_all()` before
discarding it. Band's public error does not include the upstream diagnostic,
but the Copilot SDK can emit its own traceback before Band handles a failure.

The SDK 1.x public `stop()` can erase its child references even when termination
or waiting fails, and it exposes no public process-exit handle. A narrow,
version- and type-checked teardown bridge therefore observes its known
`_process` and `_cli_process` `subprocess.Popen` handles before public `stop()`,
retains them across failures, and confirms physical exit before releasing
custody. Startup still uses the unmodified public SDK `start()` method.

```python notest
from band.adapters.copilot_acp import CopilotACPAdapter

adapter = CopilotACPAdapter()
try:
    catalog = await adapter.probe_models(timeout_s=15.0)
    model_ids = catalog.model_ids
finally:
    await adapter.cleanup_all()
```

