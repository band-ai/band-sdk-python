# ACP and native OMP integrations

Facts about the ACP and native OMP integrations that span modules or that the
code cannot say for itself. The ACP client side is `ACPClientAdapter` (Band room →
room-owned ACP subprocess); the server side is `ACPServer` +
`BandACPServerAdapter`. OMP uses a separate [native RPC integration](#omp-native-rpc),
not ACP.

Client-side automatic idle release is unavailable pending provider-specific
safety qualification; see [idle room resource retention](idle-room-release.md).

## Configuration

`ACPClientAdapterConfig` holds the plain settings; `CursorACPAdapterConfig` and
`CopilotACPAdapterConfig` extend it with backend defaults and fields. Callables
(`workspace_for_room`, `resolve_session_config`, `resolve_permission`) are
keyword-only constructor arguments.

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
- **Idle release stays off.** Cursor and Copilot report `ProviderActivity.UNKNOWN`,
  so an idle release always answers BUSY. Native qualification is still owed for
  Cursor work beyond subagent drain and Copilot scheduled `/after` prompts.
  Band MCP calls and ACP
  permission or extension requests carry no prompt id, so a call arriving after its
  turn is served with the room's current tools.

## Session config

`resolve_session_config` runs after each new or restored session and before its first
prompt. Each successful `session/set_config_option` response replaces the catalog the next
selection is validated against (choosing a model can change the reasoning levels). Any
failure fails that room turn visibly instead of falling back.

## Backends

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

## OMP native RPC

`OmpAdapter`, `OmpAdapterConfig` and `OmpPermissionRequest` live in
`band.adapters.omp` and are exported from `band.adapters`. Install `band-sdk[omp]`
and an OMP executable at version 18.8.9 or later; the executable fixture is pinned
to 18.8.9 and requires Bun 1.3.14 or later. The `omp` extra does not require the ACP
runtime. Native sessions negotiate RPC protocol v2 to preserve structured
text/image tool results.

- **Configuration.** Pass `OmpAdapterConfig` to the adapter. `model` selects
  `provider/model`, and `reasoning_effort` selects the native thinking level,
  including `"auto"`. Automatic effort stays automatic rather than being replaced
  with the effective level chosen for one turn.
  `api_key` requires `model` and reaches the child in that provider's environment
  variable; OMP does not read an `OMP_MODEL` environment variable.
  `workspace_for_room` and `resolve_permission` remain constructor callables.
  OMP does not use the ACP `resolve_session_config` callback.
- **Tools and replies.** Canonical Band tools and `additional_tools` are
  registered through native `set_host_tools`; results retain text/image content.
  Room replies still require `band_send_message`, and deliberate declines use
  `band_no_reply`. Native assistant text is optional thought telemetry, not a
  room reply. `SimpleAdapter.run_judged_turn` remains the sole turn-judging owner:
  native-only output and failed tools followed by narration still reach the
  missing-reply verdict.
- **Instructions and attribution.** A private always-apply native rule places Band's
  stable communication and completion guidance in OMP's genuine system prompt,
  preserving native instructions, user prompt additions and append-file discovery.
  A startup command verifies the rule is present before inference, even without
  external MCP servers. Disabling rules or using a system-prompt template that
  omits them fails startup rather than silently dropping Band's connection policy.
  The rule survives restored sessions and prompt rebuilds during background work.
  Per-message platform context contains
  room, requester, roster and contact facts as JSON, not `[System]`-styled commands.
  Replayed history is identified as previously handled data; it is not a new
  participant handoff. Native lifecycle fields describe runtime progress, while the
  Band turn ledger separately determines whether a reply was delivered or declined.
- **Background settlement.** Prompt acceptance, an initial `prompt_result`, and
  `agent_end` with async work remaining are not completion. The reader stays
  attached through automatic background continuations and waits for both the
  matching prompt outcome and actual native session settlement
  (`session_settled` or `get_state.isSettled`). An error in a resumed assistant
  message fails the same Band turn. This is a native lifecycle barrier, not an
  instruction-only mitigation, and it does not synthesize a reply or decline.
  `turn_timeout_s` covers the whole lifecycle. Timeout, errors and cancellation
  close that room's process and terminate its captured descendants before returning;
  a subsequent message starts a fresh process rather than inheriting unfinished work.
- **Process close.** A usable reader gets a best-effort abort request bounded to
  one second, then stdin closes for EOF while output is drained. The root gets
  one second to exit before termination and, if needed, kill, each followed by
  another one-second exit wait. Captured descendants are terminated and, if
  needed, killed concurrently with the root's EOF grace; Python running behind
  a Windows virtualenv launcher is one such descendant.
  On POSIX, termination sends `SIGTERM` and kill sends `SIGKILL`. On Windows,
  both are forced `TerminateProcess` operations, not graceful signal delivery.
  Successful close proves the root was waited for and captured descendants
  settled; it does not guarantee exit status zero. Caller cancellation still
  waits for shared cleanup, and cleanup failures propagate.
- **Isolation and state.** Each room owns a native process, persistent session
  and workspace. OMP receives the workspace via `--cwd`; the subprocess itself
  starts without a supplied cwd. Session state carries `omp_session_id`,
  `omp_session_file` and `omp_room_id`. A workspace is not an OS sandbox.
- **External MCP servers.** `mcp_servers` is a name-to-`OmpMcpServer` map; import
  that typed model from `band.integrations.omp`. Stdio entries use `command`,
  `args`, `env` and optional `cwd`; remote entries use `url`, `headers` and optional
  `type` (`http` or `sse`). Relative stdio `cwd` resolves from the room workspace;
  relative path-like commands resolve from that server cwd, or the room workspace
  when cwd is omitted, before plugin projection. The adapter writes an owner-only temporary
  `band-sdk/.mcp.json` plugin overlay and passes it with `--plugin-dir`. The overlay
  stays alive until the child closes, then is removed. User profiles and native
  credentials are not rewritten.
  The SDK requires native connection and tool-mount receipts for every configured
  server before inference. It uses the RPC UI event mode and a private startup
  setting to enable those receipts without rewriting user settings. Missing or
  failed receipts fail startup rather than allowing a model to answer without its
  required tools. Native connection and tool failures remain errors, not successful
  Band replies. The enclosing turn timeout still bounds startup.
- **Per-connection MCP servers.** Pass the constructor callable
  `resolve_mcp_servers`, an async `(workspace: str) -> Mapping[str, OmpMcpServer]`,
  when external servers (or their credentials) can change while the adapter runs.
  It is called once for each new room process, with the room's claimed workspace,
  before the overlay or process exists; the same result feeds both the overlay and
  the startup receipt check. Its result is the room's complete **external** server
  set: it replaces every external entry in `mcp_servers` (an empty mapping means
  none), and an exception fails that startup with no fallback to them. A `band`
  entry in `mcp_servers` is the SDK's own Band endpoint, not an external server: it
  is kept beside the resolved set, so Band tools still route through it with
  `inject_band_tools=False`. The resolver itself may not return `band`, because OMP
  would mint that server's tools under Band tool names. A reused room process never
  calls it again; a retired room (cancellation, failure, or cleanup) resolves afresh
  on its next start. The callable is runtime-only and never part of
  `OmpAdapterConfig`.
- **Room retirement.** `on_cleanup` starts one retirement per room, and overlapping
  `on_cleanup` and `cleanup_all` calls join it. The room's active turn is the
  current `on_message` call, not the task that made it. A cancellation already
  requested when that call starts is delivered at its first step, before anything
  is resolved or launched; one its caller had already received and handled does
  not stop it. Retirement marks the active turn retired, so nothing it resolves
  afterwards can start a process. While the turn is still running, retirement
  becomes the cause of its end: it disarms the turn deadline if that has not fired,
  so the deadline cannot cancel the turn's settlement again, and cancels the turn
  once, or joins a cancellation it is already handling. A turn whose deadline has
  fired, or that is already handling its own outcome, keeps its timeout or other
  result and its failure report. A fired deadline counts as that cancellation even
  if the resolver consumed it with `uncancel()`: cleanup joins it rather than
  cancelling again, and a server set the resolver returns afterwards is refused, so
  the turn ends with `TimeoutError` and nothing is launched. A turn that has already
  finished is never
  cancelled, so its caller's later work is untouched. Retirement then waits for
  that call's own finalization (not for the rest of its caller's work), and only
  then closes the process and releases the workspace. A turn may request cleanup of
  its own room, for example from its resolver: that call returns at once, the turn
  is not cancelled again but its unfired deadline is disarmed all the same, it stops
  before launching, and teardown follows its settlement. Cancelling a cleanup
  call stops only that caller's wait. Until the retirement ends, turns already
  waiting for the room and turns that arrive during it are cancelled rather than
  starting a new process. A later turn starts normally. A room whose native close
  fails keeps its process handle, overlay and exclusive workspace claim until a
  later close succeeds: `on_cleanup` raises the failure, `cleanup_all` and the
  room's next turn retry the close first, and the retiring process never receives
  new work.
- **Permissions.** `approval_mode="yolo"` bypasses native approval checks without
  changing Band platform permissions. Otherwise `resolve_permission` receives
  `OmpPermissionRequest(room_id, session_id, title, options)` and returns an
  offered option or `None`. Unknown or unoffered choices cancel the request.
  Without a resolver, only the exact native `("Approve", "Deny")` select is
  approved. Unsupported interactive forms are cancelled; this is not approval
  for arbitrary UI requests.
  A cancelled select posts an independent `omp_permission` call/result pair only
  with `Emit.TOOL_CALLS`. Offered selections stay silent; option labels do not
  determine an SDK denial outcome. Permission narration records no turn effect
  or chat reply. Permission titles and native events share full-registry privacy
  redaction, including `mcp__` names and executed `xd://` device writes. Bare Band
  aliases use the canonical privacy policy without relabelling native events.
  Native names and turn effects still use enabled capabilities plus contacts;
  withheld device writes retain their path, other fields and string payload.
  Unparseable arguments, including decoder integer-limit errors, use a fixed
  placeholder. Synthetic IDs use `band-permission:`; metadata retains the
  original `omp_request_id`. Native event IDs are never changed or merged with
  permission records.
  The shared file-content redactor counts UTF-8 bytes with surrogate preservation,
  so lone surrogates cannot discard a tool event in any adapter. The shared OMP
  normalizer treats all decoder `ValueError`s as unparseable executed payloads,
  retaining its existing failed-tool identity. Unexpected privacy-projection
  exceptions replace the payload with a fixed opaque placeholder without retrying;
  native identity and device wrapper fields remain intact.
  Final tool-call envelope encoding is protected too. If encoding fails, the
  record keeps its event name and ID, scalar `path`/`file_path`/`target` fields
  and an opaque arguments placeholder. Other private or nested fields are
  discarded; enabled-native turn effects remain unchanged.

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
