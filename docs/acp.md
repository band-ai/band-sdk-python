# ACP (Agent Client Protocol) Integration

Facts about the ACP integration that span modules or that the code cannot say for
itself. The client side is `ACPClientAdapter` (Band room → room-owned ACP subprocess);
the server side is `ACPServer` + `BandACPServerAdapter`.
The native OMP adapter does not use ACP; see [OMP native RPC](#omp-native-rpc).

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
  than twice on the same one. Only a bootstrap resume candidate is closed when
  its configuration is refused.
- **Closure is confirmed, not assumed.** The process handle stays on the room until
  `__aexit__` succeeds, its child is reaped and the agent's stdout reaches EOF.
  A child left running by a successful context exit is killed and waited for;
  a descendant that inherited stdout keeps the room closing until it exits, with
  a warning naming the agent's pid. A failed or cancelled exit leaves the room
  owned, and a retry kills and waits for the same process. `cleanup_all()` and
  its alias `stop()` share one
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

## Per-room MCP servers

`resolve_mcp_servers(workspace)`, a keyword-only constructor argument of
`ACPClientAdapter`, `CopilotACPAdapter` and `CursorACPAdapter`, returns native
`acp.schema` descriptors for that room's workspace.
It runs once per session setup after the agent initializes. A reused live session
keeps its servers; a restored session and its fresh-session fallback share one
deep-copied snapshot. Recovery and a deliberate wake resolve anew on the new
connection, as does a new session replacing a stale Band MCP endpoint.

The result replaces `config.mcp_servers`, including when empty, except Band routing:
a configured `band` entry and the injected Band server remain after the resolved
external servers. Callback failure never falls back to configured external servers.

- `McpServerStdio` is always accepted. `HttpMcpServer` and `SseMcpServer` require the
  agent to explicitly advertise that transport in `initialize`. `AcpMcpServer` is
  refused. Names must be unique and may not be `band`.
- A refused descriptor fails the turn with `ACPMcpServerResolutionError`, naming
  only its server and transport, never its URL, headers, command or environment.
  Resolution failures recover the room's process through its retained owner; the
  next turn resolves again on a fresh connection.
- Queued turns share the room's initializer. One cancelled waiter leaves it for
  another; the last waiter cancels it once without waiting for callback settlement.
  The initializer's retained lease holds the process and workspace until it settles.
  A callback that swallows cancellation or requests teardown cannot publish a session
  or pass its late result to the agent.
- An abandoned initializer's failure is observed even without a successor. Its
  diagnostic names only the exception type; a successor waits for settlement
  before resolving again and does not inherit the abandoned failure.
- A callback requesting `on_cleanup`, `cleanup_all` or `stop` gets
  `CleanupIncompleteError` rather than waiting on itself. The retained owner continues
  teardown after the callback unwinds, preserving the process, workspace and Band
  MCP server until closure is confirmed.
- There is no resolver timeout: a callback that never returns holds that room's
  setup, while other rooms continue independently.

## Session config

`resolve_session_config` runs after each new or restored session and before its first
prompt. Each successful `session/set_config_option` response replaces the catalog the next
selection is validated against (choosing a model can change the reasoning levels). Any
failure fails that room turn visibly instead of falling back.

A configuration callback for an accepted prompt on an acquired process may request
cleanup. It receives `CleanupIncompleteError` without cancelling its shared session
setup or the accepted prompt currently waiting for it. That prompt may finish
through native prompting beyond the cleanup grace; unrelated queued work is
cancelled normally. Teardown retains resources until the accepted work finishes.
Later work is refused. MCP resolution still cannot start a new session scope after
cleanup. Explicit caller cancellation and provider timeouts remain effective.

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

## Native OMP transport

`band.integrations.omprpc.OmpRpcClient` speaks OMP's native RPC v2 protocol,
including structured ask questions and answers. It is separate from the ACP
transport; adding it does not deprecate or remove the public `OmpACPAdapter`.

Event callbacks run serially with a bounded backlog. Exhausting that backlog
fails the owning prompt with `OmpProcessError` and starts cleanup rather than
silently dropping events and reporting success. The wire reader does not wait
for callback capacity, so callbacks can still issue correlated commands and
answer native UI requests while delivery is held.

An observed root-process exit also retires the transport when descendants still
hold stdout open. Command errors, including an error reported after prompt
admission, raise `OmpRpcError` without retiring an otherwise usable process.
Host-tool and UI callbacks admitted while a settled turn drains share its
original callback-exit deadline; an unfinished callback fails the turn and starts
cleanup. SDK debug logs report stderr byte counts, never the child's raw stderr.

Explicit MCP transports use `band.integrations.ompprocess.OmpMcpTransport`.
Native session metadata uses `band.converters.omp.OmpMetadataKey.ROOM` and
`OmpMetadataKey.SESSION`.

A `create_process` factory customizes creation without bypassing containment.
On Windows it launches the existing helper; the client still owns the Job
containing the native process and its descendants, including during cancelled
or incomplete startup.
To hold or observe the owned launch, wrap `spawn_owned_process` and delegate to
its bound original (or override it and call `super()`). Do not pass that wrapper
as `create_process`: that factory runs below the ownership boundary. The owned
method retains the process before returning, so wrapper failure still reaps it.

On POSIX, process-group capture rechecks a reaped leader's identity after
enumerating members, before retaining that group. A reused leader PID does not
transfer custody to an unrelated session. Platforms without process-group support
use their existing Job and environment-marker ownership instead of probing that
PID as a group identity.

## OMP native RPC

`OmpAdapter`, `OmpAdapterConfig` and `OmpPermissionRequest` live in
`band.adapters.omp` and are exported from `band.adapters`. Install `band-sdk[omp]`
and an OMP executable at `band.integrations.ompprocess.OMP_NATIVE_PINNED_PACKAGE`
or later (Bun 1.3.14 or later). The `omp` extra does not require the ACP runtime,
and the adapter exists beside the ACP-bridged `OmpACPAdapter`; the CI backends lane
installs one shared `omp` binary at the native pin for both. Native sessions
negotiate RPC protocol v2 to preserve structured text/image tool results.

- **Configuration.** Pass `OmpAdapterConfig` to the adapter. `model` selects
  `provider/model`, and `reasoning_effort` selects the native thinking level,
  including `"auto"`. Automatic effort stays automatic rather than being replaced
  with the effective level chosen for one turn.
  `api_key` requires `model` and reaches the child in that provider's environment
  variable; OMP does not read an `OMP_MODEL` environment variable.
  `workspace_for_room` and `resolve_permission` are constructor callables.
  The native adapter has no ACP `resolve_session_config` callback.
- **Tools and replies.** Canonical Band tools and `additional_tools` are
  registered through native `set_host_tools`; results retain text/image content.
  Room replies require `band_send_message` or a native `ask` (below), and deliberate
  declines use `band_no_reply`. Native assistant text is optional thought telemetry, not a
  room reply. `SimpleAdapter.run_judged_turn` remains the sole turn-judging owner:
  native-only output and failed tools followed by narration still reach the
  missing-reply verdict.
- **Questions go to the room.** Each session enables OMP's structured ask dialog
  with `set_ask_dialog`. An `ask` posts the model's questions, options and recommended
  choice together, mentioning the requester; the post counts as the turn's reply.
  The native tool receives an immediate acknowledgement, not a fabricated answer:
  the participant's answer arrives as a later message in the same native session.
  A room processes messages serially, so waiting inside the turn would prevent
  that answer from being consumed. Cancelling a native ask would abort the whole
  prompt. A failed post instead returns an error explanation to the model, and
  the turn still owes a reply. A platform stop for this room ends the turn without
  answering the ask; an unrelated room's stop remains a failed post.
  Other UI requests retain `resolve_permission` behavior. Shutdown rejects new
  UI and host-tool callbacks before they can post. This covers the active room's
  native ask dialog, not separately forwarded subagent UI.
- **Instructions and attribution.** A private always-apply native rule places Band's
  shared communication and completion guidance in OMP's genuine system prompt,
  preserving native instructions, user prompt additions and append-file discovery.
  A startup command verifies the rule is present before inference, even without
  external MCP servers. Disabling rules or using a system-prompt template that
  omits them fails startup rather than silently dropping Band's connection policy.
  The rule survives restored sessions and prompt rebuilds during background work.
  Per-message platform context contains room, requester, roster and contact facts
  as JSON. Replayed history is identified as previously handled data; it is not a
  new participant handoff.
- **Background settlement.** Prompt acceptance, an initial `prompt_result`, and
  `agent_end` with async work remaining are not completion. The reader stays
  attached through automatic background continuations and waits for both the
  matching prompt outcome and actual native session settlement
  (`session_settled` or `get_state.isSettled`). An error in a resumed assistant
  message fails the same Band turn. `turn_timeout_s` covers the whole lifecycle.
  Timeout, errors and cancellation run cleanup for that room's process; a
  subsequent message starts a fresh native process rather than reusing the
  failed one. Cancelling a prompt still waiting for admission does not
  interrupt the active prompt.
- **Process close.** A usable reader gets a best-effort abort request bounded to
  one second, then stdin closes for EOF while output is drained. The root gets
  one second to exit before forced cleanup, which still runs if the root has
  already exited. POSIX cleanup kills the process group established at spawn
  and discovers additional descendants through a per-launch inherited
  environment marker. Signals validate captured process identities to avoid
  killing unrelated processes. This discovery is best effort: a detached
  descendant whose environment is replaced or hidden by the OS may remain
  undiscovered and outlive close. It is not an OS containment guarantee or a
  security sandbox. Windows establishes job membership before the command runs
  and retains the job handle through cleanup. Successful close confirms root
  exit and settlement of identified members, not a zero exit status or exit
  of every possible POSIX descendant. Caller cancellation still waits for
  shared cleanup, and cleanup failures propagate. A failed close retains the
  room, startup files and exclusive workspace claim; cleanup or the next turn
  retries that same close before releasing resources or starting a fresh process.
  A model or reasoning selection accepted while close is blocked is kept for the
  replacement process, whether cleanup is retried explicitly or by the next turn.
- **Isolation and state.** Each room owns a native process, persistent session
  and workspace. OMP receives the workspace via `--cwd`; the subprocess itself
  starts without a supplied cwd. Session state carries `omp_session_id`,
  `omp_session_file` and `omp_room_id`. A workspace is not an OS sandbox.
- **External MCP servers.** `mcp_servers` is a name-to-`OmpMcpServer` map. Import
  `OmpMcpServer` and `OmpMcpTransport` from `band.integrations.ompprocess`;
  use `OmpMcpTransport` members for Python `type` arguments. Stdio entries use
  `command`, `args`, `env` and optional `cwd`; remote entries use `url`, `headers`
  and optional `type` (`http` or `sse`). Relative stdio `cwd` resolves from the
  room workspace; relative path-like commands resolve from that server cwd, or
  the room workspace when cwd is omitted, before plugin projection. The adapter
  writes an owner-only temporary `band-sdk/.mcp.json` plugin overlay and passes
  it with `--plugin-dir`. The overlay stays alive until the child closes, then is
  removed. User profiles and native credentials are not rewritten.
  The SDK requires native connection receipts for every configured server before
  inference. It uses the RPC UI event mode and a private startup setting to
  enable those receipts without rewriting user settings. Missing or failed
  receipts fail startup rather than allowing a model to answer without its
  required tools. The enclosing turn timeout still bounds startup.
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
  starting a new process. Once cleanup succeeds, a later turn can start normally.
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
  redaction, including `mcp__` names and executed `xd://` device writes. Native
  names and turn effects still use enabled capabilities plus contacts; withheld
  device writes retain their path, other fields and string payload. Unparseable
  arguments use a fixed placeholder. Synthetic IDs use `band-permission:`;
  metadata retains the original `omp_request_id`. Native event IDs are never
  changed or merged with permission records. If encoding a tool-call envelope
  fails, the record keeps its event name and ID, scalar `path`/`file_path`/`target`
  fields and an opaque arguments placeholder.

### Native ask wire smoke

From a source checkout with the `omp` extra installed, exercise an installed
OMP executable's structured dialog and native answer validation:

```bash
PYTHONPATH=src:packages/band-mcp/src:. python -m tests.integrations.ompasksmoke --executable omp
```

The executable must support native RPC v2 and `set_ask_dialog` (18.8.9 or later)
and allow the explicit test extension to load. This uses a temporary native
session and an extension command, not a model prompt or a Band room; no Band
credentials are used and no inference is spent. Native startup still loads
the installation's provider/model configuration.

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
