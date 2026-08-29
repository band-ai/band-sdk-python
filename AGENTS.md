# Band Python SDK

This is a Python SDK that connects AI agents to the Band collaborative platform.

## Core Features

1. Multi-framework support (LangGraph, Anthropic, CrewAI, Claude SDK, Copilot SDK, Codex, Pydantic AI, Parlant, Gemini, Letta, Google ADK, OpenCode, Agno, Strands Agents)
2. A2A protocol support: Bridge to remote A2A agents and expose Band peers as A2A endpoints
3. ACP integration: Editor-facing server and client adapters over stdio or TCP (Cursor, Codex, Claude Code, GitHub Copilot)
4. Platform tools for chat, contacts, memory, and file management
5. WebSocket + REST transport: Real-time messaging with REST API fallback

## Platform Tools

### Tool text is never written in an adapter

`src/band/runtime/tools.py` owns every word an LLM reads about a platform tool:
the input model's class docstring is the tool description, and each
`Field(description=...)` is an argument description. An adapter must reach for
whichever of these fits its framework instead of retyping the text:

| Framework wants | Use | Result |
|---|---|---|
| A Pydantic `args_schema` class | `platform_args_schema(name)` | the master model itself |
| The same, but its tool layer emits a value the master won't parse | `platform_args_schema(name, validators={...})` | a subclass with the master's text plus the extra validators |
| Schema derived from a function docstring | `@platform_tool` (bare — reads `fn.__name__`, takes no name argument) | docstring = master description + a rendered `Args:` section |
| A raw JSON/dict schema | `iter_tool_definitions()`, `get_openai_tool_schemas()`, `get_anthropic_tool_schemas()` | built live from the master |

None of these accept description text — an adapter that needs different wording
has a modeling problem to fix on the master, not a local string to write. If a
framework's leniency really is adapter-specific (CrewAI's `mentions` coercion),
express it as a `validators=` entry, never as a re-declared field.

```python
from band.runtime.tools import SendMessageInput, platform_args_schema, platform_tool


@platform_tool
async def band_send_message(content: str, mentions: list[str]) -> None: ...


assert platform_args_schema("band_send_message") is SendMessageInput
assert "Args:" in (band_send_message.__doc__ or "")
```

Guardrail: `tests/framework_conformance/test_tool_text_drift.py` runs each
`AdapterConfig.advertised_arg_text` probe and fails if what the adapter
advertises differs from the master. Wire the probe up for a new adapter that
builds its own schema objects; leave it `None` when the master schema is passed
through untouched.

### Chat Tools
- `band_send_message`: Send message to chat room (requires mentions)
- `band_send_event`: Send non-message event (thought, error, task)
- `band_add_participant`: Add agent/user to room
- `band_remove_participant`: Remove participant from room
- `band_get_participants`: List room participants
- `band_lookup_peers`: Find available agents/users
- `band_create_chatroom`: Create new chat room

### Contact Tools
- `band_list_contacts`: List agent's contacts with pagination
- `band_add_contact`: Send contact request to add someone
- `band_remove_contact`: Remove existing contact
- `band_list_contact_requests`: List received and sent requests
- `band_respond_contact_request`: Approve, reject, or cancel requests

### Memory Tools
- `band_list_memories`: List memories with filters (scope, system, type)
- `band_store_memory`: Store new memory with content, system, type, segment
- `band_get_memory`: Retrieve a specific memory by ID
- `band_supersede_memory`: Mark memory as superseded (soft delete)
- `band_archive_memory`: Archive memory (hide but preserve)

### File Tools
- `band_list_room_files`: List files attached to any message in the room, paginated
- `band_read_room_file`: Read a file — inline text/image for small previewable files, a description otherwise
- `band_send_room_file`: Upload text content as a file and share it in the room

## Adapter Feature Flags (emit / capabilities)

Every adapter constructor takes `emit=`, `capabilities=`, `include_tools=`,
`exclude_tools=`, `include_categories=` directly (`**features:
Unpack[FeatureKwargs]`), never a wrapping `AdapterFeatures(...)` object:

```python notest
adapter = ClaudeSDKAdapter(model="...", emit=Emit.TOOL_CALLS | Emit.THOUGHTS)
adapter = AgnoAdapter(agent, capabilities=Capability.MEMORY)
```

`Emit` and `Capability` are `StrEnum`s whose members combine with `|` into a
`frozenset`; a lone member is also accepted directly (no set literal needed).

- **`emit` is opt-out**: omitted, it defaults to everything the adapter's
  `SUPPORTED_EMIT` declares (tool-call narration, thoughts, task events,
  usage — whichever that adapter supports). Pass `emit=()` for silence, or a
  narrower `Emit` combination to select specific kinds.
- **`capabilities` is opt-in**: omitted, it defaults to empty. Turning on
  `Capability.MEMORY`/`Capability.CONTACTS`/`Capability.FILES` puts extra tool
  schemas in front of the model on every turn, so it stays off by default.
- Requesting an `emit`/`capabilities` value outside the adapter's
  `SUPPORTED_EMIT`/`SUPPORTED_CAPABILITIES` raises `BandConfigError`
  immediately at construction — never a silent no-op.
- `Emit.TASK_EVENTS` is load-bearing, not just narration, on Codex/Letta/
  Opencode: each persists its session/thread/agent-resume mapping in task
  event metadata gated by that flag. Narrowing `emit` to exclude it also
  stops resumption across restarts — see the class docstring on each of
  those three adapters before doing so.

## Capability Negotiation Against Platform Feature Flags

`Capability.FILES` gates the three file tools above, but declaring it isn't
enough by itself: the platform's room-file storage (`ff_file_transfer`) is an
**on-prem-only deployment flag, off everywhere on SaaS today** — never enable
`Capability.FILES` in an example or a default config, since it would
silently do nothing (or worse, look wired up) against the hosted platform.

`src/band/runtime/capabilities.py` is the single source of truth mapping a
`Capability` to the `AgentMe.feature_flags` key that gates it
(`CAPABILITY_FEATURE_FLAGS`), plus the pure `prune_unsupported(features,
feature_flags)` function:

- `feature_flags is None` (the `/me` fetch never ran or failed) → keep
  whatever was requested; no information is not a basis to refuse.
- `feature_flags` present, key `True` → keep the capability.
- `feature_flags` present, key `False` **or missing entirely** → prune it. A
  missing key means the connected deployment predates that capability, which
  is exactly as unsupported as an explicit `False`.

`Agent.start()` and `OneShotInvoker.startup()` both call
`adapter.apply_effective_features(prune_unsupported(adapter.features,
runtime.feature_flags))` right after fetching identity, but only when the
adapter is a `SimpleAdapter` (a bare `FrameworkAdapter` has no
`SUPPORTED_CAPABILITIES` and can't request a gated capability in the first
place). `apply_effective_features` is a `SimpleAdapter` hook whose default
body just reassigns `self.features`; an adapter that caches something
derived from capabilities at construction time (`OpencodeAdapter`,
`ACPClientAdapter`, `LettaAdapter`) would need to override it to rebuild that
cache too — none do yet, since none of them declare `Capability.FILES`.
`SlackAdapter` overrides it to also delegate into the wrapped inner adapter,
since its own `_resolve_features()` only mirrors features into the inner
adapter once, at construction.

**First (and only) adapter wired to `Capability.FILES`: `claude_sdk`.** It's
also the only adapter with the vision-passthrough fix that lets
`band_read_room_file`'s image branch reach the model as real vision input
(`{"content": [{"type": "image", ...}]}`) instead of being `json.dumps`'d
into a text block — see `_is_mcp_content_result`/`_make_result` in
`src/band/integrations/claude_sdk/tools.py`. Every other adapter, and the
published `band-mcp` CLI (whose `--tools` vocabulary has no `files` group),
stays out of scope until a later pass gives it the same treatment.

`AgentTools.get_tool_schemas`/`get_anthropic_tool_schemas`/
`get_openai_tool_schemas` and `iter_tool_definitions` take a single
`capabilities: frozenset[Capability] | None` parameter — the boolean
`include_memory`/`include_contacts` pair they used to take is gone
(breaking change, no back-compat shim). `None` resolves to the pre-existing
default (contacts only); the hub-room execution path still unions
`Capability.CONTACTS` in regardless of what was requested.

## REST Client API Pattern

The SDK uses Fern-generated REST client with property-based namespace API:

```python notest
# Pattern: agent_api_<resource>.method()
await link.rest.agent_api_chats.create_agent_chat(...)
await link.rest.agent_api_messages.create_agent_chat_message(...)
await link.rest.agent_api_participants.list_agent_chat_participants(...)
```

**Sub-clients**: `identity`, `peers`, `contacts`, `chats`, `messages`, `events`, `participants`, `context`, `memories`, `files`, `profile`, `agents`

## WebSocket Channels & Events

### Channels (Phoenix Channels Protocol V2)

| Channel | Topic Format | Events |
|---------|--------------|--------|
| Agent Rooms | `agent_rooms:{agent_id}` | `room_added`, `room_removed` |
| Chat Room | `chat_room:{chat_room_id}` | `message_created` |
| User Rooms | `user_rooms:{user_id}` | `room_added`, `room_removed` |
| Room Participants | `room_participants:{chat_room_id}` | `participant_added`, `participant_removed` |
| Tasks | `tasks:{user_id}` | `task_created`, `task_updated` |

### Payload Models (Pydantic)

Field rules and normalization (alias sync, defaulting, coercion) live in
`band-sdk-core` (`band_sdk_core.validate_event_payload`), not in these
models — they are rule-free typed projections, hydrated without
re-validating by `WirePayload.from_wire` (`src/band/client/streaming/wire.py`).
Every model inherits `WirePayload`, which sets `ConfigDict(extra="allow")`
once for all of them.

```python notest
MessageCreatedPayload:
  id, content, message_type, sender_id, sender_type,
  sender_name?, metadata? (MessageMetadata), chat_room_id?,
  thread_id?, inserted_at, updated_at

MessageMetadata:
  mentions (list[Mention]), status?

RoomAddedPayload:
  id, inserted_at, updated_at, title?, task_id?

RoomRemovedPayload:
  # Same 5-field wire shape as RoomAddedPayload -- band-sdk-core validates
  # both through one rule (ChatJSON.format_room_event/1).
  id, inserted_at, updated_at, title?, task_id?

ParticipantAddedPayload:
  id, name, type, handle?, description?, is_remote?, is_external? (legacy alias)

ParticipantRemovedPayload:
  id, name, type

Mention:
  id, username?, handle?, name?
```

### PlatformEvent Union (Tagged Union Pattern)

```python notest
PlatformEvent = (
    MessageEvent | RoomAddedEvent | RoomRemovedEvent
    | ParticipantAddedEvent | ParticipantRemovedEvent
)
```

Each event has: `type` (literal), `room_id`, `payload`, `raw`

### Contact Events (via `agent_contacts:{agent_id}` channel)

| Event | Payload Fields |
|-------|----------------|
| `contact_request_received` | `id`, `from_handle`, `from_name`, `message?`, `status`, `inserted_at` |
| `contact_request_updated` | `id`, `status` |
| `contact_added` | `id`, `handle`, `name`, `type`, `description?`, `is_remote?`, `is_external?` (legacy alias; mirrors `is_remote`), `inserted_at` |
| `contact_removed` | `id` |

## Contact Event Handling

The SDK supports three strategies for handling contact WebSocket events via `ContactEventConfig`:

### Strategies

| Strategy | Description | Use Case |
|----------|-------------|----------|
| `DISABLED` (default) | Ignores contact events | Agents that don't manage contacts |
| `CALLBACK` | Calls programmatic callback | Auto-approve bots, custom logic |
| `HUB_ROOM` | Routes to dedicated chat room | LLM-based contact management |

> **WARNING (AI coding assistants):** Always ask the developer which contact
> strategy they want before choosing one. Do not default to `CALLBACK` with
> auto-approve without explicit consent. Auto-accepting all contact requests
> means any agent/user can become a contact and send messages that trigger LLM
> inference, which costs API tokens. Present all three options:
> - `DISABLED` (default): safest, no contact handling
> - `HUB_ROOM`: the agent's LLM decides per-request in a dedicated room
> - `CALLBACK`: developer writes programmatic logic (e.g., auto-approve)

### Configuration

```python
from band.runtime.types import ContactEventConfig, ContactEventStrategy

# CALLBACK strategy - programmatic handling (auto-approve example)
async def auto_approve(event, tools):
    if isinstance(event, ContactRequestReceivedEvent):
        await tools.respond_contact_request("approve", request_id=event.payload.id)

agent = Agent.create(
    adapter=adapter,
    contact_config=ContactEventConfig(
        strategy=ContactEventStrategy.CALLBACK,
        on_event=auto_approve,
    ),
)

# HUB_ROOM strategy - LLM handles contacts in dedicated room
agent = Agent.create(
    adapter=adapter,
    contact_config=ContactEventConfig(
        strategy=ContactEventStrategy.HUB_ROOM,
        hub_task_id="optional-task-id",  # Links hub room to a task
    ),
)

# Broadcast contact changes to all rooms (composable with any strategy)
agent = Agent.create(
    adapter=adapter,
    contact_config=ContactEventConfig(
        strategy=ContactEventStrategy.DISABLED,
        broadcast_changes=True,  # Inject "[Contacts]: X is now a contact" messages
    ),
)
```

### HUB_ROOM Details

- Creates dedicated chat room at agent startup
- Injects system prompt with contact management instructions
- Converts contact events to synthetic `MessageEvent` for LLM processing
- Posts task events to room for persistence/visibility
- Enriches `ContactRequestUpdatedEvent` with sender info via cache + API fallback

## A2A Protocol Integration

The SDK supports the [A2A (Agent-to-Agent) protocol](https://google.github.io/A2A/) in two directions:

### A2A Adapter (outbound)

`A2AAdapter` forwards Band messages to a remote A2A-compliant agent. Each Band room maps to an A2A context, with automatic session state persistence via task events and session rehydration on room rejoin.

```python
from band.adapters.a2a import A2AAdapter, A2AAuth

adapter = A2AAdapter(
    remote_url="http://localhost:10000",
    auth=A2AAuth(api_key="..."),  # optional
)
```

### A2A Gateway (inbound)

`A2AGatewayAdapter` + `GatewayServer` expose Band peers as A2A JSON-RPC endpoints. Remote A2A clients can send messages to Band agents via the gateway, with context ID preservation (same `contextId` = same chat room) and SSE streaming responses.

```python
from band.adapters.a2a_gateway import A2AGatewayAdapter, GatewayServer

adapter = A2AGatewayAdapter(port=10000)
```

### Key files

| Purpose | Path |
|---|---|
| A2A Adapter | `src/band/adapters/a2a.py`, `src/band/integrations/a2a/adapter.py` |
| A2A Gateway | `src/band/adapters/a2a_gateway.py`, `src/band/integrations/a2a/gateway/` |
| A2A Types | `src/band/integrations/a2a/types.py` |

## MCP Engine

One MCP-framework-neutral engine (`src/band/integrations/mcp/engine.py`)
builds every Band MCP tool registration; two front doors consume it instead
of each hand-rolling their own FastMCP/lowlevel-Server wiring:

| Front door | Module | Runs |
|---|---|---|
| Published CLI | `packages/band-mcp` (`band_mcp.server`, `band_mcp.shared`) | Standalone `band-mcp` process, stdio or SSE, against a real Band room over REST |
| Embedded server | `src/band/integrations/mcp/local_server.py` (`LocalMCPServer`) | In-process, for adapters that need to hand an already-live `AgentTools` to an external agent (OpenCode, the desktop app, ACP client sessions) |

`EngineSpec`/`MCPToolRegistration` describe *what* to register (name,
description, Pydantic input model, an async `execute`); `build_engine(spec)`
turns that into a real `FastMCP` instance. Each front door supplies its own
`ToolsResolver` (a single `invoke(definition, chat_id, arguments)` method)
that decides *how* a call reaches Band:

- `EmbeddedResolver` (embedded door): no cache, resolves straight to a
  caller-supplied `AgentTools`/`AgentToolsProtocol`.
- `StandaloneResolver` (`band_mcp.shared`, CLI door): human-tools singleton
  dispatch plus an LRU-cached (128), lock-striped (64) per-room `AgentTools`
  pool, since one CLI process can serve many rooms over its lifetime.

The model-facing argument is always `chat_id`, never `room_id` — the
Python-side variable/field is still `room_id` throughout the codebase, only
the text the model sees (tool descriptions, schema field names, prompt
blocks like OpenCode's per-turn Room Context) says `chat_id`.

**MCP-package imports are confined to an explicit allowlist**
(`tests/mcp/test_import_boundary.py`): `engine.py`, `local_server.py`,
`desktop_app/server.py`, and `band_mcp/{shared,server}.py`. This is enforced
by an AST scan, not a convention — it exists so an MCP Python SDK major-
version migration only has to touch those five files, not audit the tree for
stray `mcp`-package imports. A new module that genuinely needs to import
`mcp` directly belongs on that allowlist with a comment saying why; anything
else should go through the engine or a resolver instead.

**The published CLI's wire contract is pinned by declarative code, not a
snapshot.** `tests/mcp/test_wire_contract.py` drives a real `list_tools()`
round trip and checks it against small, hand-written `ToolContract`
entries — only tools with a genuinely non-obvious wire invariant (an
enum, an array item type, a required-set) get one; enum values are read
from the real `StrEnum`/`Literal` they come from, never copied by hand.
`chat_id` room-binding (required when unpinned, hidden when pinned) is
checked once, generically, against `AGENT_ROOM_BOUND_TOOL_NAMES`.

## OpenCode Integration

`OpencodeAdapter` maps each Band room to an OpenCode session on a running
`opencode serve`: room messages become prompts, and the server's SSE stream is
relayed back as room messages, tool narration, and error events.

| Purpose | Path |
|---|---|
| Adapter package | `src/band/adapters/opencode/{adapter,approvals,config}.py` |
| Typed SSE events | `src/band/integrations/opencode/events.py` |
| HTTP/SSE client | `src/band/integrations/opencode/client.py` |

Four invariants are easy to break and expensive to rediscover:

- **Band tools are never gated.** A `permission.asked` naming one of the
  adapter's own registered tools is auto-approved with `always` in *every*
  `approval_mode` (codex parity — it runs band tools with no gate). Only
  non-tool asks, such as OpenCode's `doom_loop` heuristic, follow the mode, so a
  headless room with no approver should run `approval_mode="auto_accept"`.
- **OpenCode prefixes MCP tools with the server name** (`band_store_memory`
  surfaces as `<server>_band_store_memory`). Reported `tool_call`/`tool_result`
  names are canonicalized back, so consumers match one vocabulary.
- **One `serve` is shared by every agent on the host**, and it keys MCP
  registrations globally by name. Each agent registers under a name derived from
  its Band identity, and every prompt scopes tool visibility to that
  registration (deny the shared namespace, then re-allow its own — OpenCode
  applies the last matching rule).
- **The model is told the current `chat_id` every turn.** The band MCP tools'
  schemas require it, so without the per-turn Room Context block the platform
  tools are uncallable.

`turn_timeout_s` bounds *compute*: time parked on a manual approval is excluded,
since the ask carries its own `approval_wait_timeout_s` expiry.

## ACP (Agent Client Protocol) Integration

ACP enables editors (Zed, Cursor, JetBrains, Neovim) to communicate with AI agents via JSON-RPC over stdio. The SDK provides both server and client sides.

### Architecture

Two-layer pattern (mirrors A2A Gateway):

| Layer | Server Side | Client Side |
|-------|-------------|-------------|
| Protocol | `ACPServer` (JSON-RPC handler) | ACP SDK's `spawn_agent_process` |
| Platform Bridge | `BandACPServerAdapter` | `ACPClientAdapter` |

**Server**: Editor -> ACP -> `ACPServer` -> `BandACPServerAdapter` -> Band REST/WS -> Peers
**Client**: Band room message -> `ACPClientAdapter` -> stdio subprocess **or** TCP connection (Codex, Claude Code, Cursor, GitHub Copilot, etc.)

### Key Files

| File | Purpose |
|------|---------|
| `src/band/integrations/acp/server.py` | `ACPServer` — handles ACP JSON-RPC methods, does not subclass `acp.Agent`; `run_acp_server` — runs it with `use_unstable_protocol` (required for `session/fork`, `session/resume`, `session/close`) |
| `src/band/integrations/acp/server_adapter.py` | `BandACPServerAdapter` — REST client, room/session mapping |
| `src/band/integrations/acp/client_adapter.py` | `ACPClientAdapter` — drives a remote ACP agent over stdio-spawn or TCP-connect |
| `src/band/integrations/acp/client_runtime.py` | `ACPRuntime` (transport-agnostic) + `ACPCollectingClient` (session_update parsing / coalescing / collapse / live sink), `tcp_spawn_process` (TCP connect seam) |
| `src/band/integrations/acp/room_emitter.py` | `RoomTurnEmitter` — posts a turn's chunks to the room in causal order; `turn_replied_in_room` (text-fallback suppression) |
| `src/band/adapters/copilot_acp.py` | `CopilotACPAdapter` — thin `ACPClientAdapter` for the GitHub Copilot CLI |
| `src/band/integrations/acp/client_types.py` | `BandACPClient` — thin `ACPCollectingClient` subclass |
| `src/band/integrations/acp/router.py` | `AgentRouter` — slash commands and mode-based routing |
| `src/band/integrations/acp/push_handler.py` | `ACPPushHandler` — unsolicited session_update notifications |
| `src/band/integrations/acp/event_converter.py` | `EventConverter` — PlatformMessage -> ACP session_update chunks |
| `src/band/integrations/acp/cli.py` | `band-acp` CLI entry point |
| `src/band/converters/acp_server.py` | History converter for server adapter |
| `src/band/converters/acp_client.py` | History converter for client adapter |

### CLI

```bash
# Installed via pip/uv as console_scripts entry point
band-acp --agent-id my-agent --api-key $BAND_API_KEY

# Or with environment variables
BAND_AGENT_ID=my-agent BAND_API_KEY=key band-acp
```

### Session Lifecycle

1. Editor connects via stdio -> `ACPServer.on_connect()` stores client ref
2. `new_session(cwd, mcp_servers)` -> creates Band room, stores cwd/mcp_servers per session
3. `prompt(blocks, session_id)` -> extracts text/image/resource content, sends to room, waits for `done_event`
4. `on_message()` receives peer response -> `EventConverter.convert()` -> `session_update` back to editor
5. `on_cleanup(room_id)` -> removes all session state, unblocks pending prompts

### Live, causally-ordered emission (Client Adapter)

A turn's events must land in the room in the order they happened, because two things post **live, mid-turn**: a Band messaging tool's own room post (a remote/injected band-mcp calling REST as it runs), and a denied-permission pair. So `ACPCollectingClient` doesn't buffer-then-flush — it **streams** finalized chunks to a per-session live sink (`set_sink`) as `session_update`s arrive:

- Consecutive text/thought deltas coalesce into one run, finalized at the next boundary or the turn-end `flush`.
- A call's `tool_call_update` frames fold by `tool_call_id` into one result, finalized once the call reports a terminal status (`completed`/`failed`).
- The buffer (`_session_chunks`) still accumulates the finalized chunks — the per-turn record `get_collected_chunks` returns, cleared each turn by `reset_session` (in-memory, not durable) and keyed per session so concurrent rooms don't need a global lock.

`RoomTurnEmitter` (`room_emitter.py`) is the sink: it posts narration (thought/tool_call/tool_result/plan) live for **every** tool call — including Band messaging tools, with no suppression — and holds **only** the assistant text until close (the text-fallback decision needs the whole turn). `ACPRuntime.prompt(..., on_chunk=emitter.emit)` registers the sink and `flush`es at turn end.

### History replay fallback (Client Adapter)

A **freshly created** ACP session owes the room a transcript replay; a restored one
does not. On bootstrap the adapter first validates the room's persisted session id
with ACP `session/load`; on any miss (no persisted id, unavailable, or erroring
load) the fresh session is seeded with the room's text transcript
(`ACPClientSessionState.replay_messages`, built by the shared
`build_replay_messages` helper in `converters/helpers.py`). A session minted
**off-bootstrap** (the previous runtime was torn down mid-run, e.g. after a prompt
failure) re-fetches the transcript itself via `tools.fetch_room_context`, so a
respawn never starts amnesiac. Replay is injected exactly once into the session's
first prompt under `HISTORY_REPLAY_HEADER`: framed as read-only background (treat as
already handled; never re-execute), with the current message attributed and last
under a nonce'd `[New Message <nonce>]` boundary marker the header names (the nonce
defeats a replayed message spoofing the boundary). Bootstrap history stops
**strictly before** the triggering message (`messages_before` in
`runtime/formatters.py`, applied in `preprocessing/default.py` for every adapter):
later backlog entries are pending turns of their own and never replay. Adapter
narration events (thought/tool_call/tool_result/task) never replay. A successfully
loaded session gets no replay, so history is never doubled.

### Reply Delivery (Client Adapter)

Tool-first with a text fallback, matching `copilot_sdk`/`codex`: if the turn posted via a Band messaging tool, the agent's plain text is **not** also relayed; otherwise the held text is relayed at turn close. The decision lives in `turn_replied_in_room()` (`room_emitter.py`), which reads the collected tool-call stream — the ACP adapter can't flip an in-process flag like the siblings, because its tools may execute out-of-process (remote band-mcp), so it matches `tool_call` title + `completed` status. Which tools count is defined once in `is_room_posting_tool()` / `ROOM_POSTING_TOOL_NAMES` (`src/band/runtime/tools.py`): the SDK's `band_send_message` (also what band-mcp 1.3.2+ advertises, since its registrar reuses the SDK tool definitions) plus the legacy `create_agent_chat_message` spelling from band-mcp ≤1.3.1. This suppression is about the text fallback only — the call's own `tool_call`/`tool_result` narration (below) is never suppressed.

### Tool narration (Client Adapter)

Every tool call is narrated as `tool_call`/`tool_result`, including Band messaging tools (`band_send_message`/`band_send_event`) — there is no "self-reporting" special case. Because emission is live and causally ordered (above), a Band messaging tool's own room post lands *between* its `tool_call` and `tool_result` narration, so the room naturally reads `tool_call -> message -> tool_result` without any special-casing.

Narrated names are canonical: an ACP runtime that prefixes MCP tool names (Copilot registers the loopback server's tools as `band-<tool>`) has the prefix stripped at chunk construction when the name reveals one of the adapter's own registered tools (`canonicalize_mcp_tool_name` in `src/band/runtime/tools.py`, sharing one resolver with `is_room_posting_tool`). Foreign tool names pass through untouched.

### Capabilities (Client Adapter)

`ACPClientAdapter` supports `Capability.MEMORY` and `Capability.CONTACTS`. Only memory tools are gated on the declared capability (an enterprise feature the adapter must opt into); contact tools register unconditionally, matching the adapter's pre-existing default that every caller without `features=` (every ACP example) relies on — declaring `Capability.CONTACTS` only stops the base class's unsupported-capability warning for a caller that does declare it. The registered tool vocabulary (computed once at construction) drives tool-name canonicalization too. `render_system_prompt` carries the matching capability sections.

### Permission pairing (Client Adapter)

Auto-approval grants silently — no event posts for an approved request, ordinary or Band tool alike; the call's real `tool_call`/`tool_result` narration (above) is the visible record. Only a **denied** request posts a synthetic `tool_call`/`tool_result` pair (`RoomTurnEmitter.open_permission`), since the tool never runs and there is nothing else to show it happened.

### Optional Dependency

```toml
[project.optional-dependencies]
acp = ["agent-client-protocol"]
```

Install with: `pip install band-sdk[acp]` or `uv add band-sdk[acp]`

### Client transports (stdio / TCP)

`ACPClientAdapter` selects a transport at construction; both flow through `ACPRuntime`'s
injectable `spawn_process` seam, so the runtime and downstream code are transport-agnostic.

- **stdio** (default): pass `command=[...]` to spawn the agent as a subprocess
  (`acp.spawn_agent_process`).
- **TCP**: pass `host=` + `port=` to connect to an already-running ACP server
  (`tcp_spawn_process` → `asyncio.open_connection` → `acp.connect_to_agent`). Use for an
  ACP agent in a remote/containerized environment.
- Exactly one of `{command, (host, port)}` is required (validated in `__init__`).
- Advanced: inject a custom `spawn_process` (e.g. `docker exec -i … copilot --acp`, ssh,
  or a fake in tests). Tests inject a fake through this seam rather than patching module
  globals (see `tests/integrations/acp/conftest.py::FakeSpawn` / the `make_acp_transport`
  fixture).

### GitHub Copilot CLI backend

`CopilotACPAdapter` (`src/band/adapters/copilot_acp.py`) drives `copilot --acp` through
`ACPClientAdapter`. Copilot speaks vanilla ACP (no `copilot/*` extension methods → no custom
profile). Auth is flexible — an env token (`COPILOT_GITHUB_TOKEN`>`GH_TOKEN`>`GITHUB_TOKEN`),
a stored `copilot login`, `gh`, or BYOK; for stdio pass any of it via the config `env`
(`github_token` is a convenience for `GITHUB_TOKEN`), unset to use the ambient login.
Registered in the baseline matrix under the `backends` lane, gated on the CLI + the
Anthropic key: the baseline builder spawns it Anthropic-BYOK (`COPILOT_PROVIDER_*` env,
see `copilot_acp_env` in `tests/e2e/baseline/toolkit/builders.py`) so lane runs don't
burn the monthly Copilot-hosted quota, and BYOK mode needs no GitHub auth. One bespoke
smoke (`test_copilot_hosted_auth_replies`) keeps the Copilot-hosted auth path proven
with a single turn; it reads `GITHUB_TOKEN` and skips when unset. Excluded from
framework-conformance as a bridge.

- stdio example: `examples/acp/clients/copilot.py`.
- Copilot-in-a-container over TCP + Band tools via a `band-mcp` (SSE) server:
  `examples/acp/copilot_docker/compose/` (separate services) and
  `examples/acp/copilot_docker/colocated/` (single container). Both use
  `inject_band_tools=False` + an explicit `mcp_servers` SSE URL, since a remote Copilot
  can't reach the SDK host's loopback `LocalMCPServer`.
- Copilot in a Docker **microVM sandbox** ([`sbx`](https://docs.docker.com/ai/sandboxes/))
  over stdio (`sbx exec -i <sandbox> copilot --acp`): `examples/acp/copilot_sandbox/` —
  isolation + a host-side secret proxy (token never enters the VM). Uses the ordinary
  stdio transport; auth is out-of-band via `sbx secret set -g github`.

## REST Client OMIT vs Null

When calling REST endpoints with optional parameters, **never pass `None`** - the Fern client sends `null` which fails backend validation. Instead, use kwargs:

```python fixture:client
# WRONG - sends {"action": "approve", "handle": null, "request_id": "..."}
await client.agent_api_contacts.respond_to_agent_contact_request(action="approve", handle=None, request_id="...")

# CORRECT - sends {"action": "approve", "request_id": "..."}
kwargs = {"action": "approve", "request_id": "..."}
await client.agent_api_contacts.respond_to_agent_contact_request(**kwargs)
```

## Workarounds for band-client-rest Bugs

`band-client-rest` is pinned exactly (`pyproject.toml`, currently `==0.0.27`). Before
writing a workaround, check whether a newer release already fixes it upstream:

- `pip index versions band-client-rest`, then diff the relevant model/method
  (`uv pip install band-client-rest==<newer> --target /tmp/check`).
- Already fixed upstream → bump the pin. Default action, not a suggestion.
  Only write a workaround after confirming the bump is actually blocked (cite
  the blocker: failing CI, unresolved conflict) — "inconvenient" isn't one.
- Still needed → tie it to the pin: comment naming the exact version where
  it stops being reachable, so it's not silently dead code after the next bump.
- A test against the real dependency (not a stubbed exception) doubles as
  that tripwire. Check the CI status is real, though — a grouped Dependabot
  bump can fail at collection from an unrelated package first, hiding it.

Example (PR #531): a `resolve_handle` workaround for missing `data.id` was
scoped to `0.0.10`. `0.0.15` already dropped the `id` field from
`ResolvedEntity` upstream. Bumped straight to `0.0.26` (the pin has since moved
further), deleted the workaround — no version guard needed once the fix is
already upstream.

## Comment Style

Comments state facts about the code as it is now, not narration of how it
got there — never "extracted from X", "ported from Y", "changed from Z",
no session/PR/ticket/line-number history. Git already owns that history.

A comment earns its place only by saying something a reader can't get
from the code itself: a non-obvious invariant, a race/ordering guarantee,
a workaround for a specific external bug, a scope boundary that looks
like it should be wider than it is. Never restate what the code already
says in prose.

If a function needs a long comment to be understood, that's a signal the
function itself is doing too much — split it into named sub-functions
whose names carry the "what," and keep the comment for the one "why" that
can't be named away. Prefer trimming/removing this class of comment
outright over compressing it.

## Code Structure

```
src/band/
├── adapters/       # Framework adapters (langgraph, anthropic, crewai, a2a, etc.)
├── converters/     # History converters per framework
├── core/           # Protocols, types, base classes
├── runtime/        # Execution context, tools, formatters
├── platform/       # WebSocket/REST transport, events
├── preprocessing/  # Event filtering before adapter
├── client/         # Low-level API clients
├── integrations/   # Deep framework integrations (a2a, acp, anthropic, claude_sdk, langgraph, parlant, pydantic_ai)
├── config/         # Configuration management, YAML loading, env parsing
├── testing/        # Testing utilities (fake tools, test helpers)
└── agent.py        # Main entry point
```

## Testing Structure

```
tests/
├── adapters/       # Unit tests per adapter (mocked)
├── converters/     # Unit tests per converter
├── core/           # Core logic tests
├── runtime/        # Runtime tests
├── integration/    # Real API tests (skipped in CI)
├── e2e/            # End-to-end tests (requires live platform + LLM keys)
│   └── baseline/   # The only E2E suite: reusable toolkit + smokes (see baseline/README.md)
├── skills/         # Tests for .claude/skills scripts (paths via tests/paths.py anchors)
└── conftest.py     # Shared fixtures
```

`testpaths = ["tests"]`, so **every** test lives here — including tests for code
outside `src/band` (`band-bridge` -> `tests/bridge`, `docker/band_python_kit` ->
`tests/docker`, `.claude/skills` -> `tests/skills`). A `test_*.py` placed next to
non-package code is never collected by CI's bare `uv run pytest`; address the code
under test through an anchor in `tests/paths.py` instead.

Before writing a new E2E test or helper, read `tests/e2e/baseline/README.md`
— it documents the reusable baseline toolkit (provisioning, user ops, reply
capture, judge, assertions, fixtures) so you reuse it instead of rebuilding it.
To wire a new framework adapter into the matrix, follow
`tests/e2e/baseline/ADDING_AN_ADAPTER.md`.

## Commands

```bash
# Install dependencies (all extras except crewai and parlant — see Dependency Conflicts below)
uv sync --extra dev

# Install crewai adapter deps (isolated from dev/parlant/pydantic-ai)
uv sync --extra dev-crewai

# Install parlant adapter deps (isolated from dev/crewai/pydantic-ai)
uv sync --extra dev-parlant

# Run unit tests
uv run pytest tests/ --ignore=tests/integration/ --ignore=tests/e2e/ -v

# Run single test
uv run pytest tests/ -k "test_name"

# Run with coverage
uv run pytest tests/ --ignore=tests/integration/ --ignore=tests/e2e/ --cov=src/band

# Run integration tests (requires API key)
uv run pytest tests/integration/ -v -s --no-cov

# Run E2E tests (requires live platform + LLM API keys)
E2E_TESTS_ENABLED=true uv run pytest tests/e2e/ -v -s --no-cov

# Run E2E tests for a single adapter
E2E_TESTS_ENABLED=true uv run pytest tests/e2e/ -k langgraph -v -s --no-cov

# Run the baseline toolkit smokes (provision their own agents; only need
# BAND_API_KEY_USER — see tests/e2e/baseline/README.md)
E2E_TESTS_ENABLED=true uv run pytest tests/e2e/baseline/ -v -s --no-cov

# Linting and formatting
uv run ruff check .
uv run ruff format .
uv run pyrefly check
```

## Dependency Conflicts

**crewai cannot coexist** with parlant or pydantic-ai in the same Python
environment due to conflicting transitive dependencies:

| Conflict | crewai requires | Other package requires |
|---|---|---|
| pydantic | `<2.13` | pydantic-ai-slim 2.x needs `>=2.12` |
| opentelemetry-sdk | `~=1.42.0` | parlant needs `>=1.37` |

This is declared in `pyproject.toml` via `[tool.uv] conflicts` so `uv lock`
resolves each in a separate fork.

**parlant cannot coexist with pydantic-ai** either, for an unrelated reason: it's a
namespace collision, not a version ceiling. `parlant` depends on the `griffe`
distribution; `pydantic-ai-slim` depends on `griffelib` — two different PyPI
distributions that both install files into the same `griffe` import path.
Installing both corrupts that path (whichever wheel's files land last wins per
file, nondeterministic by install order). Also declared via `[tool.uv] conflicts`.
Separately, `parlant` itself pulls `fastmcp` (a `griffelib` dependency as of
`fastmcp>=3.2.4`) alongside its own direct `griffe` dependency, so a `[tool.uv]
constraint-dependencies` entry pins `fastmcp>=3.2.0,<3.2.4` — otherwise parlant
collides with itself even with pydantic-ai nowhere in the picture.

**Extras layout:**
- `dev` — includes all framework deps **except** crewai and parlant
- `dev-crewai` — includes crewai + test tooling only (no parlant/pydantic-ai)
- `dev-parlant` — includes parlant + test tooling only (no crewai/pydantic-ai)
- `crewai` is mutually exclusive with `parlant` and `pydantic-ai` runtime extras
- `parlant` is mutually exclusive with `pydantic-ai` (the griffe/griffelib clash above)

**For CI:** crewai adapter tests require a separate job/step using
`uv sync --extra dev-crewai`; parlant adapter tests likewise use
`uv sync --extra dev-parlant` (`test-parlant` job).

## Environment Variables

When running examples, live probes, integration checks, or provisioning
against a real Band platform, load these from the repo-root `.env.test` —
not ad-hoc `.env` copies, shell leftovers, or invented values — and never
print secret values from it. Example-local `.env` files (e.g.
`examples/**/.env`) may still hold Docker/`GITHUB_TOKEN` config, but Band
agent keys and platform URLs should stay aligned with `.env.test` /
`agent_config.yaml` rather than a second source of truth.

- `BAND_REST_URL`: REST API URL (default: https://app.band.ai)
- `BAND_WS_URL`: WebSocket URL (default: wss://app.band.ai/api/v1/socket/websocket)
- `BAND_API_KEY_USER`: User API key for E2E WebSocket observer and trigger messages (the only Band key the baseline toolkit needs — it provisions its own agents)
- `BAND_API_KEY_USER_2`: Optional second user key, for baseline smokes exercising two-user interaction
- `OPENAI_API_KEY`: OpenAI API key (for LangGraph examples)
- `ANTHROPIC_API_KEY`: Anthropic API key (for Anthropic/Claude SDK examples)
- `GOOGLE_API_KEY`: Google API key for Gemini Developer API (for Gemini/Google ADK examples)
- `GOOGLE_GENAI_USE_VERTEXAI`: Set to `true` to use Vertex AI instead of Gemini Developer API
- `GOOGLE_CLOUD_PROJECT`: Google Cloud project ID (required when using Vertex AI)
- `GITHUB_TOKEN`: A Copilot-entitled GitHub token. The baseline `copilot_sdk` and `copilot_acp` builders use Anthropic BYOK and never read it; the only baseline reader is the single Copilot-hosted auth smoke (`test_copilot_hosted_auth_replies`, skips when unset). Also used by Copilot-hosted examples outside the baseline; optional when a stored `copilot login` is present.
- `E2E_TESTS_ENABLED`: Set to `true` to enable E2E tests (default: disabled)
- `E2E_LLM_MODEL`: OpenAI model for E2E tests (default: `gpt-5.4-mini`)
- `E2E_ANTHROPIC_MODEL`: Anthropic model for E2E tests (default: `claude-haiku-4-5` — the baseline judge uses structured outputs, which older Haiku models do not support)
- `E2E_JUDGE_MODEL`: Anthropic model for the baseline LLM judge (default: falls back to `E2E_ANTHROPIC_MODEL`; must support structured outputs)
- `E2E_TIMEOUT`: Per-turn response timeout in seconds for E2E tests (default: `120`; a slow test can add headroom with `@pytest.mark.timeout(extra=n)`)
- `DOCKER_TESTS_ENABLED`: Set to `true` to run `docker_build`-marked tests (e.g. `tests/docker/test_band_python_kit.py`), which shell out to a real `docker build`/`docker run` (default: disabled everywhere, including CI — CI runners do have a Docker daemon, unlike the nested-virtualization `sbx` tests, so this needs the same explicit opt-in as `E2E_TESTS_ENABLED` rather than a plain Docker-availability check)

Baseline lane scoping (see `tests/e2e/baseline/README.md`):

- `BAND_E2E_LANE`: The CI lane (a job: a `uv` extra + optional server/CLI setup) to scope the run to. Lane ids are content-based and decoupled from the `uv` extra — `core` (anthropic/openai-family adapters plus `copilot_sdk`, which self-downloads its CLI runtime and uses Anthropic BYOK without GitHub auth; `dev` extra), `crewai` (`dev-crewai` extra), `google` (gemini/google_adk, split out for rate-limit isolation), `backends` (codex + opencode coding agents), `letta` (self-hosted letta server), `parlant` (`dev-parlant` extra — split from `core` because parlant's griffe/griffelib transitive deps collide with pydantic_ai's; registers no matrix adapter, a bespoke `@lane`-pinned smoke only). Resolves the lane's adapters from the registry (`ci_lanes()`, derived from each adapter's `requires`); out-of-lane adapters skip-with-reason (they're covered by their own lane) while in-lane adapters keep fail-loud (an unwired backend stays red). Unset (the local default) = full matrix, no scoping. CI never lists adapters — it derives lanes from the registry. A test's lane is derived from **all** the frameworks it touches (a matrix cell's adapter plus its `@per_adapter(peer=...)`, or a `@with_adapters` set); a test whose frameworks span more than one home lane fails collection (`assert_every_item_is_schedulable`) unless pinned with `@lane(Lane.X)` to a lane whose extra hosts them all. To add a lane, see `tests/e2e/baseline/README.md` ("Adding a CI lane").

Baseline provisioning/cleanup policy (see `tests/e2e/baseline/README.md`):

- `BAND_E2E_AUTOCLEAN`: Reap provisioned agents + rooms on teardown (default: `true`; set `false` to keep resources for debugging a failing run)
- `BAND_E2E_ORPHAN_SWEEP`: Sweep leftover agents from crashed prior runs at session start (default: `true`)
- `BAND_E2E_ORPHAN_MAX_AGE_MINUTES`: Only sweep agents older than this, so a concurrent run is never reaped mid-flight (default: `120`)
- `BAND_E2E_SCORECARD_JSON`: Write this run's adapter×test scorecard (pass/fail/skip + N/A reasons) as JSON to this path at session end (default: empty = don't emit). CI sets one path per lane; a final job merges them (see `tests/e2e/baseline/scorecard.py` and the Scorecard section of the baseline README)

## Adding a New Framework Integration

When adding a new framework adapter and converter, follow this TDD workflow. Use the lowercase module name (e.g. `openai`, `gemini`) and derive the PascalCase class prefix (e.g. `OpenAI`, `Gemini`).

### Phase 1: Scaffold Source Files

1. Create converter at `src/band/converters/<framework>.py` — class `{Framework}HistoryConverter` with stub `convert()`, `set_agent_name()`, `__init__(*, agent_name=None)`. Use `from band.converters.parsing import parse_tool_call, parse_tool_result`.
2. Create adapter at `src/band/adapters/<framework>.py` — class `{Framework}Adapter` extending `SimpleAdapter[T]` with `__init__` params: `model`, `custom_section`, `history_converter`, `**features: Unpack[FeatureKwargs]`. Stub `on_message`, `on_started`, `on_cleanup`.
3. If the framework needs an external SDK, add an optional dependency group in `pyproject.toml`.

### Phase 2: Register with Conformance Infrastructure

1. Add an output adapter in `tests/framework_configs/output_adapters.py` — choose base class matching output format (`BaseDictListOutputAdapter`, `StringOutputAdapter`, `SenderDictListAdapter`, or `LangChainOutputAdapter`).
2. Register converter config in `tests/framework_configs/converters.py` — factory function, builder function returning `ConverterConfig` with behavioral flags, append to `_CONVERTER_CONFIG_BUILDERS`.
3. Register adapter config in `tests/framework_configs/adapters.py` — factory function with mocked constructor args, builder function returning `AdapterConfig`, append to `_ADAPTER_CONFIG_BUILDERS`. If the adapter builds its own tool schema objects, also set `advertised_arg_text` (see [Tool text is never written in an adapter](#tool-text-is-never-written-in-an-adapter)).

### Phase 3: Run Conformance Tests (Expect Failures)

```bash
uv run pytest tests/framework_conformance/test_config_drift.py -v
uv run pytest tests/framework_conformance/test_adapter_conformance.py -v -k "<framework>"
uv run pytest tests/framework_conformance/test_converter_conformance.py -v -k "<framework>"
```

### Phase 4: Implement the Converter

In `src/band/converters/<framework>.py`, implement `convert()`: text messages as `[sender_name]: content`, own agent filtering, other agent remapping, tool events via `parse_tool_call`/`parse_tool_result`, skip thought messages, default role to `"user"`.

### Phase 5: Implement the Adapter

In `src/band/adapters/<framework>.py`: `on_started` sets agent name/description and creates client, `on_message` converts history and invokes LLM, `on_cleanup` cleans per-room state safely.

### Phase 6: Write Framework-Specific Tests

- Adapter tests in `tests/adapters/test_<framework>_adapter.py` — LLM invocation, tool execution, error handling, custom tools.
- Converter tests in `tests/converters/test_<framework>.py` — tool event format, batching, malformed input.

### Phase 7: Final Validation

```bash
uv run pytest tests/framework_conformance/ tests/framework_configs/ -v
uv run pytest tests/adapters/test_<framework>_adapter.py tests/converters/test_<framework>.py -v
uv run pytest tests/ --ignore=tests/integration/ --ignore=tests/e2e/ -v
uv run ruff check . && uv run ruff format .
```

### Key Files Reference

| Purpose | Path |
|---|---|
| Adapter source | `src/band/adapters/<framework>.py` |
| Converter source | `src/band/converters/<framework>.py` |
| Adapter config registry | `tests/framework_configs/adapters.py` |
| Converter config registry | `tests/framework_configs/converters.py` |
| Output adapters | `tests/framework_configs/output_adapters.py` |
| Adapter conformance tests | `tests/framework_conformance/test_adapter_conformance.py` |
| Converter conformance tests | `tests/framework_conformance/test_converter_conformance.py` |
| Config drift detection | `tests/framework_conformance/test_config_drift.py` |

## Example Files (examples/ directory)

### PEP 723 Script Metadata (Required for `uv run` support)

Every example file must include PEP 723 inline script metadata at the top for standalone execution with `uv run`:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[<extra>]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Brief description of what this example does.

Run with:
    uv run examples/<framework>/<example_file>.py
"""
```

Replace `<extra>` with the appropriate framework extra (e.g., `langgraph`, `anthropic`, `crewai`, `claude-sdk`, `pydantic-ai`, `parlant`).

### Other Requirements

- Use `load_agent_config("agent_name")` for credentials, NOT direct `os.environ.get()`
- Never read `BAND_WS_URL`/`BAND_REST_URL` by hand — `Agent.create`/`from_config`
  resolve them (explicit arg > env > production default via
  `band.config.PlatformSettings`); just call `load_dotenv()` and omit
  `ws_url`/`rest_url` (guarded by `tests/example_agents/test_surface_guards.py`)
- Run the agent with `async with agent: await agent.run_forever()` — the
  lifecycle style all examples showcase (`await agent.run()` is equivalent)
- Use `raise ValueError(...)` for missing required config, NOT `logger.error()` + `sys.exit()`
- Use single sys.path line: `sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))`
- Never hardcode UUIDs in docstrings - reference `agent_config.yaml` instead
- All `async def main()` functions must have `-> None` return type hint
- Always include `from __future__ import annotations` as first import

## Documentation Testing (markdown snippets)

Tracked `.md` files (except `examples/`) run in CI as tests via `pytest-markdown-docs`
— so `python` snippets in the docs must stay correct and runnable, not rot:

```bash
# What CI runs (ci.yml):
uv run pytest --markdown-docs $(git ls-files '*.md' ':!:examples/*') --no-cov
# One file, verbose, while iterating:
uv run pytest --markdown-docs path/to/FILE.md --no-cov -v
```

Fence conventions (the language tag after the opening ```` ``` ````):

- ` ```python ` — **executed**. The block is a test: top-level `assert`s are the
  checks; any unhandled exception fails CI.
- ` ```python notest ` — **not executed** (collected out). Use only for illustrative
  pseudo-code, placeholder names (`MyframeworkAdapter`, `MYPROVIDER`), or snippets
  that genuinely need a live platform/LLM.
- ` ```python fixture:<name> ` — executed with the named pytest fixture injected into
  the block's namespace (precedent: `fixture:client`, `fixture:agent_config_path`).
  The fixture is resolved from the nearest `conftest.py`.

**Prefer runnable over `notest`.** If a snippet only needs importable symbols (types,
enums, helpers), drop `notest` and add a small `assert` so a rename breaks the doc.
Reach for `fixture:` when it needs a constructed object (a client, a config path).

**Gotcha — snippets under `tests/e2e/**` skip in CI.** That tree's conftest skips
every collected item (code fences included) unless `E2E_TESTS_ENABLED=true`, and the
CI markdown-docs step does **not** set it. So a `python` block in an E2E doc silently
*skips* in CI and protects nothing — worse than honest `notest`. Keep E2E-doc snippets
`notest`; if you want a runnable check of E2E-adjacent symbols (e.g. "these registry
helpers still exist"), put it in a doc **outside** `tests/e2e/**` or in a real unit
test, where the markdown-docs run actually executes it.

## Coding Standards

- Always use type hints for function parameters and return types
- Use `from __future__ import annotations` as the first import in every file
- **Imports go at the top of the file, full absolute path (`from band.x.y
  import Z`), never inside a function body.** This gets missed constantly —
  check it explicitly before finishing any edit that touches an import. The
  one legitimate exception: a module gated behind an optional extra not
  installed in every lane's venv (e.g. `band.adapters.copilot_acp` imports
  `acp`, the `agent-client-protocol` package — importing it at module level
  would break test *collection* for a venv that lacks that extra, such as
  `dev-crewai`). Even then the deferred import belongs only at the specific
  call site that needs it, and only because collection-time safety genuinely
  requires it — never as a default habit. If the module has no such
  extra-gated dependency (true for the vast majority, including every
  adapter that only shells out to a CLI, like `codex`), the import is
  top-level, full stop.
- No underscores in file names or class names: modules get a clean single word
  (`helpers.py`, not `_utils.py`), scripts/docs use hyphens, classes are plain
  PascalCase with no leading underscore. Exception: patterns a tool requires,
  e.g. pytest's `test_*.py` / `conftest.py`.
- Never read configuration with `os.environ` / `os.getenv` — define a
  `pydantic-settings` `BaseSettings` class (field name == env var name,
  `SettingsConfigDict(extra="ignore", case_sensitive=False, env_ignore_empty=True)`
  — the last so a set-but-empty var like `CI=` falls back to the field default
  instead of raising a bool/int ValidationError) and read its fields; see
  `tests/e2e/baseline/settings.py` for the canonical pattern
- In tests, never derive repository-anchored paths with per-file
  `Path(__file__).parents[N]` arithmetic — import the anchors from
  `tests/paths.py` (`REPO_ROOT`, `SRC_ROOT`, `EXAMPLES_ROOT`, `KIT_DIR`,
  `ENV_TEST_FILE`). Only genuinely package-relative paths (a fixture file
  next to its test) stay relative to their own `__file__`.
- Prefer `match`/`case` over long `if`/`elif` chains that dispatch on one value
- Never use `print()` — use `logging` with module-level `logger = logging.getLogger(__name__)`
- Use `%s` placeholders in log messages for lazy evaluation
- Use Pydantic v2 for data models; use `model_dump()` not `dict()`
- Target Python 3.11+; use `list[str]` not `List[str]`, `str | None` not `Optional[str]`
- Use async/await everywhere in async codebases; use `AsyncMock` for testing async methods
- Catch `pydantic.ValidationError` separately from generic `Exception`
- Use `raise ValueError(...)` for missing required config, not `logger.error()` + `sys.exit()`
- Never put issue-tracker references in code — no Linear issue IDs (e.g. `INT-123`), Linear URLs, or ticket numbers in comments, docstrings, or strings. Explain the *why* in plain terms instead. (Branch names, commit messages, and PR descriptions may reference issues.)
- Test what really matters — behavior that can break. Don't write tests that
  restate definitions (asserting dataclass defaults equal themselves, echoing a
  constant) or otherwise cannot fail for a real reason; they add maintenance
  cost without protection.
- Write intent-oriented code: the reader should see *what* is meant, not decode
  *how* it's done. Name for intent, keep flow obvious (guard clauses, `match`,
  early returns over nested branches), and hide bookkeeping behind a small helper
  or property with an intent-revealing name. Branch on *what to do*, not *which
  function to call*, and prefer computing the varying part once over
  duplicating a call across both branches of an `if`/`else` — e.g. a log
  statement that only varies by level: `level = logging.DEBUG if known else
  logging.WARNING; logger.log(level, msg, ...)`, never `log = logger.debug if
  known else logger.warning; log(msg, ...)` (a ternary-selected callable) and
  never `if known: logger.debug(msg, ...) else: logger.warning(msg, ...)`
  (the message and args retyped in both branches).
- **Tests must be declarative and intent-revealing, not a transcript of the
  implementation.** Assert on a readable projection of the observable outcome
  — the thing the test is actually about — never on raw internals or on a
  side effect that merely implies the real answer. Concretely:
  - `assert reply.outline == ["tool_call (permission)", "message", ...]` over
    a hand-rolled comprehension pulling `message_type` out of each event dict.
  - `assert record.levelno == logging.DEBUG` over inferring a log level
    indirectly from whether two separate capture windows came back empty.
  If writing the assertion requires re-deriving *how* the code decided
  something, the test is checking the wrong thing — assert the decision
  itself.
- Prefer a single source of truth for a value or closed vocabulary consumed in more
  than one place: give it one definition — a constant, a `StrEnum`, or a small helper
  — that every site references, rather than re-typing the same magic literal in a
  producer and the consumer that reads it (a typo then fails silently). Keep genuinely
  distinct vocabularies separate, though — don't merge two sets that only happen to
  share some values today (e.g. the ACP `ChunkType` a chunk carries vs. the platform
  `message_type` an event is posted under).
- Comments should describe the code as it is, not narrate what changed between versions.

## Pre-Commit Checklist

Before running the commands below, re-read your own diff once against the
Coding Standards above — including code you just wrote this session, not only
code you started from. The two rules that get skipped under time pressure:

- **Single source of truth**: a literal, magic string, or multi-line block
  re-typed in more than one place (a second copy you just wrote counts) instead
  of one `StrEnum` / constant / small helper every site references.
- **Intent-oriented code**: a raw comprehension or dict-poke standing in for a
  small, intent-named helper — e.g. `[e for e in tools.events_sent if
  e["message_type"] == "x"]` repeated at each call site instead of one
  `events_of_type(tools, "x")`.

Ruff/pyrefly/pytest catch correctness and style; they do not catch either of
these, so this step is the only gate for them.

```bash
uv run ruff check .
uv run ruff format .
uv run pyrefly check
uv run pytest tests/ --ignore=tests/integration/ --ignore=tests/e2e/ -v
```


## Error Handling

### Pydantic ValidationError

Catch `pydantic.ValidationError` separately from generic `Exception` (see Coding
Standards). Beyond that:

- Format validation errors for LLM readability: `"Invalid arguments for tool_name: field: message"`
- Handle ValidationError at the lowest common point to avoid duplication
- Log full error details but return concise messages to LLM

Example:
```python notest
from pydantic import ValidationError

try:
    result = Model(**data)
except ValidationError as e:
    # Log full details for debugging
    logger.error(f"Validation failed: {e}")
    # Return concise message for LLM
    errors = "; ".join(f"{err['loc'][0]}: {err['msg']}" for err in e.errors())
    return f"Invalid arguments for {tool_name}: {errors}"
except Exception as e:
    logger.exception(f"Unexpected error: {e}")
    raise
```

### Exception Hierarchy

- Use specific exceptions over generic ones
- Create custom exception classes for domain-specific errors
- Always include context in exception messages

### Error Messages

- Make error messages actionable and clear
- Include relevant context (what failed, why, what to do)
- Avoid exposing internal implementation details to end users

### Required Configuration

`raise ValueError(...)` for missing required config, not `logger.error()` +
`sys.exit()` (see Coding Standards) — fail fast with a clear message:
```python notest
# Good
if not api_key:
    raise ValueError("OPENAI_API_KEY environment variable is required")

# Bad
if not api_key:
    logger.error("Missing API key")
    sys.exit(1)
```


## Git Workflow

### Branch Naming

Branch names should match the Linear issue:

- Format: `<prefix>/<title>-<ISSUE-ID>`
- Example: `feat/add-user-auth-ENG-123`

Prefixes:

- `feat/` - New features
- `fix/` - Bug fixes
- `refactor/` - Code refactoring
- `docs/` - Documentation changes
- `chore/` - Maintenance tasks

#### Creating Branches from Linear Issues

Use `git lb` to create properly named branches from Linear issues:

```bash
git lb INT-84
```

This automatically fetches the issue title from Linear and creates a branch with the correct naming convention.

If `git lb` is not installed, ask the developer for the proper branch name.

### Commit Messages

Follow conventional commits format for all commits:

```
<type>: <description>

[optional body]

[optional footer]
```

Types:
- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation only
- `refactor:` - Code refactoring
- `test:` - Adding or updating tests
- `chore:` - Maintenance tasks

### Pull Request Titles

PR titles MUST use conventional commits format:

- `feat:` - New features
- `fix:` - Bug fixes
- `docs:` - Documentation changes

Examples:
- `feat: Add custom tools support to all adapters`
- `fix: Handle validation errors in execute_tool_call`
- `docs: Update README with new adapter examples`

### Pre-Commit Checklist

See [Pre-Commit Checklist](#pre-commit-checklist) above — one checklist, not two.

### Code Review

- Keep PRs focused and reasonably sized
- Respond to review comments promptly
- Squash commits when merging if history is messy


## GitHub PR Inline Comments

### Adding Inline Review Comments

Use the GitHub Reviews API via `gh api repos/{owner}/{repo}/pulls/{pr_number}/reviews`
(`--method POST --input -`, JSON piped through a heredoc) — see "Example: Full
Workflow" below for the exact shape (`commit_id`, `event`, `body`, `comments[]`
with `path`/`line`/`body`).

### Getting the Correct Line Numbers

**Important:** Line numbers must be from the NEW version of the file, not diff line numbers.

1. Get the commit SHA:
   ```bash
   gh pr view {pr_number} --json headRefOid -q .headRefOid
   ```

2. Find correct line numbers in the actual file:
   ```bash
   # Get the file content at the PR's HEAD commit
   curl -s "https://raw.githubusercontent.com/{owner}/{repo}/{commit_sha}/path/to/file.py" | grep -n "pattern"
   ```

3. Alternatively, use the diff with grep:
   ```bash
   gh pr diff {pr_number} | grep -n "pattern_to_find"
   ```
   Note: These are diff line numbers, not file line numbers. Use the actual file method above for accuracy.

### Common Mistakes to Avoid

- **Don't use `gh pr review --comment`** - This adds a general comment, not inline comments
- **Don't use diff line numbers** - Use actual file line numbers from the new version
- **Don't use `-f` flag for JSON arrays** - Pass JSON via stdin with `--input -`
- **Don't guess line numbers** - Always verify by checking the actual file content

### Example: Full Workflow

Get the commit SHA, find line numbers in the real file, then post one review with
one or more inline comments:

```bash
# 1. Get commit SHA
COMMIT=$(gh pr view 83 --json headRefOid -q .headRefOid)

# 2. Find the line number for a specific pattern
curl -s "https://raw.githubusercontent.com/owner/repo/${COMMIT}/src/file.py" | grep -n "def my_function"

# 3. Add inline comments at those lines (a review can carry more than one)
cat << 'EOF' | gh api repos/owner/repo/pulls/83/reviews --method POST --input -
{
  "commit_id": "abc123...",
  "event": "COMMENT",
  "body": "Review with multiple comments",
  "comments": [
    {
      "path": "src/file.py",
      "line": 14,
      "body": "First comment"
    },
    {
      "path": "src/file.py",
      "line": 42,
      "body": "Second comment"
    },
    {
      "path": "src/other_file.py",
      "line": 10,
      "body": "Comment on different file"
    }
  ]
}
EOF
```

### Review Events

The `event` field can be:
- `"COMMENT"` - Submit general feedback without approval
- `"APPROVE"` - Approve the PR
- `"REQUEST_CHANGES"` - Request changes before merging
