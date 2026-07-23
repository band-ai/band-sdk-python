# Band Python SDK

This is a Python SDK that connects AI agents to the Band collaborative platform.

## Core Features

1. Multi-framework support (LangGraph, Anthropic, CrewAI, Claude SDK, Copilot SDK, Codex, Pydantic AI, Parlant, Gemini, Letta, Google ADK, OpenCode, Agno)
2. A2A protocol support: Bridge to remote A2A agents and expose Band peers as A2A endpoints
3. ACP integration: Editor-facing server and client adapters over stdio or TCP (Cursor, Codex, Claude Code, GitHub Copilot)
4. Platform tools for chat, contacts, and memory management
5. WebSocket + REST transport: Real-time messaging with REST API fallback

## Platform Tools

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

## REST Client API Pattern

The SDK uses Fern-generated REST client with property-based namespace API:

```python notest
# Pattern: agent_api_<resource>.method()
await link.rest.agent_api_chats.create_agent_chat(...)
await link.rest.agent_api_messages.create_agent_chat_message(...)
await link.rest.agent_api_participants.list_agent_chat_participants(...)
```

**Sub-clients**: `identity`, `peers`, `contacts`, `chats`, `messages`, `events`, `participants`, `context`, `memories`, `profile`, `agents`

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

All models use `ConfigDict(extra="allow")` to accept additional fields from the backend.

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
  id, status?, type?, title?, removed_at?

ParticipantAddedPayload:
  id, name, type, is_remote?, is_external? (legacy alias)

ParticipantRemovedPayload:
  id

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
| `src/band/integrations/acp/server.py` | `ACPServer` — ACP Agent subclass handling JSON-RPC |
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

### Reply Delivery (Client Adapter)

Tool-first with a text fallback, matching `copilot_sdk`/`codex`: if the turn posted via a Band messaging tool, the agent's plain text is **not** also relayed; otherwise the held text is relayed at turn close. The decision lives in `turn_replied_in_room()` (`room_emitter.py`), which reads the collected tool-call stream — the ACP adapter can't flip an in-process flag like the siblings, because its tools may execute out-of-process (remote band-mcp), so it matches `tool_call` title + `completed` status. Which tools count is defined once in `is_room_posting_tool()` / `ROOM_POSTING_TOOL_NAMES` (`src/band/runtime/tools.py`): the SDK's `band_send_message` (also what band-mcp 1.3.2+ advertises, since its registrar reuses the SDK tool definitions) plus the legacy `create_agent_chat_message` spelling from band-mcp ≤1.3.1. This suppression is about the text fallback only — the call's own `tool_call`/`tool_result` narration (below) is never suppressed.

### Tool narration (Client Adapter)

Every tool call is narrated as `tool_call`/`tool_result`, including Band messaging tools (`band_send_message`/`band_send_event`) — there is no "self-reporting" special case. Because emission is live and causally ordered (above), a Band messaging tool's own room post lands *between* its `tool_call` and `tool_result` narration, so the room naturally reads `tool_call -> message -> tool_result` without any special-casing.

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
Registered in the baseline matrix under the `backends` lane (gated on the CLI only, like
codex — auth is out-of-band); excluded from framework-conformance as a bridge.

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
└── conftest.py     # Shared fixtures
```

Before writing a new E2E test or helper, read `tests/e2e/baseline/README.md`
— it documents the reusable baseline toolkit (provisioning, user ops, reply
capture, judge, assertions, fixtures) so you reuse it instead of rebuilding it.
To wire a new framework adapter into the matrix, follow
`tests/e2e/baseline/ADDING_AN_ADAPTER.md`.

## Commands

```bash
# Install dependencies (all extras except crewai — see Dependency Conflicts below)
uv sync --extra dev

# Install crewai adapter deps (isolated from dev/parlant/pydantic-ai)
uv sync --extra dev-crewai

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

| Conflict | crewai 1.14.3 requires | Other package requires |
|---|---|---|
| pydantic | `~=2.11.9` (<2.12) | pydantic-ai-slim >=1.61 needs `>=2.12` |
| opentelemetry-sdk | `~=1.34.0` (<1.35) | parlant >=3.1 needs `>=1.37` |

This is declared in `pyproject.toml` via `[tool.uv] conflicts` so `uv lock`
resolves each in a separate fork.

**Extras layout:**
- `dev` — includes all framework deps **except** crewai
- `dev-crewai` — includes crewai + test tooling only (no parlant/pydantic-ai)
- `crewai` is mutually exclusive with `parlant` and `pydantic-ai` runtime extras

**For CI:** crewai adapter tests require a separate job/step using
`uv sync --extra dev-crewai`.

## Environment Variables

- `BAND_REST_URL`: REST API URL (default: https://app.band.ai)
- `BAND_WS_URL`: WebSocket URL (default: wss://app.band.ai/api/v1/socket/websocket)
- `BAND_API_KEY_USER`: User API key for E2E WebSocket observer and trigger messages (the only Band key the baseline toolkit needs — it provisions its own agents)
- `BAND_API_KEY_USER_2`: Optional second user key, for baseline smokes exercising two-user interaction
- `OPENAI_API_KEY`: OpenAI API key (for LangGraph examples)
- `ANTHROPIC_API_KEY`: Anthropic API key (for Anthropic/Claude SDK examples)
- `GOOGLE_API_KEY`: Google API key for Gemini Developer API (for Gemini/Google ADK examples)
- `GOOGLE_GENAI_USE_VERTEXAI`: Set to `true` to use Vertex AI instead of Gemini Developer API
- `GOOGLE_CLOUD_PROJECT`: Google Cloud project ID (required when using Vertex AI)
- `GITHUB_TOKEN`: A Copilot-entitled GitHub token for the `copilot_sdk` and `copilot_acp` adapters' runtime auth (BYOK inference reuses `ANTHROPIC_API_KEY`). Optional when a stored `copilot login` is present; used for headless/CI. Read by the baseline toolkit's `tests/e2e/baseline/settings.py`
- `E2E_TESTS_ENABLED`: Set to `true` to enable E2E tests (default: disabled)
- `E2E_LLM_MODEL`: OpenAI model for E2E tests (default: `gpt-5.4-mini`)
- `E2E_ANTHROPIC_MODEL`: Anthropic model for E2E tests (legacy E2E default: `claude-3-haiku-20240307`; baseline toolkit default: `claude-haiku-4-5` — the baseline judge uses structured outputs, which `claude-3-haiku-20240307` does not support)
- `E2E_JUDGE_MODEL`: Anthropic model for the baseline LLM judge (default: falls back to `E2E_ANTHROPIC_MODEL`; must support structured outputs)
- `E2E_TIMEOUT`: Per-turn response timeout in seconds for E2E tests (default: `120`; a slow test can add headroom with `@pytest.mark.timeout(extra=n)`)
- `DOCKER_TESTS_ENABLED`: Set to `true` to run `docker_build`-marked tests (e.g. `tests/docker/test_band_python_kit.py`), which shell out to a real `docker build`/`docker run` (default: disabled everywhere, including CI — CI runners do have a Docker daemon, unlike the nested-virtualization `sbx` tests, so this needs the same explicit opt-in as `E2E_TESTS_ENABLED` rather than a plain Docker-availability check)

Baseline lane scoping (see `tests/e2e/baseline/README.md`):

- `BAND_E2E_LANE`: The CI lane (a job: a `uv` extra + optional server/CLI setup) to scope the run to. Lane ids are content-based and decoupled from the `uv` extra — `core` (anthropic/openai-family adapters plus `copilot_sdk`, which self-downloads its CLI runtime and authenticates via a stored `copilot login` or a Copilot-entitled `GITHUB_TOKEN`; `dev` extra), `crewai` (`dev-crewai` extra), `google` (gemini/google_adk, split out for rate-limit isolation), `backends` (codex + opencode coding agents), `letta` (self-hosted letta server). Resolves the lane's adapters from the registry (`ci_lanes()`, derived from each adapter's `requires`); out-of-lane adapters skip-with-reason (they're covered by their own lane) while in-lane adapters keep fail-loud (an unwired backend stays red). Unset (the local default) = full matrix, no scoping. CI never lists adapters — it derives lanes from the registry. A test's lane is derived from **all** the frameworks it touches (a matrix cell's adapter plus its `@per_adapter(peer=...)`, or a `@with_adapters` set); a test whose frameworks span more than one home lane fails collection (`assert_every_item_is_schedulable`) unless pinned with `@lane(Lane.X)` to a lane whose extra hosts them all. To add a lane, see `tests/e2e/baseline/README.md` ("Adding a CI lane").

Baseline provisioning/cleanup policy (see `tests/e2e/baseline/README.md`):

- `BAND_E2E_AUTOCLEAN`: Reap provisioned agents + rooms on teardown (default: `true`; set `false` to keep resources for debugging a failing run)
- `BAND_E2E_ORPHAN_SWEEP`: Sweep leftover agents from crashed prior runs at session start (default: `true`)
- `BAND_E2E_ORPHAN_MAX_AGE_MINUTES`: Only sweep agents older than this, so a concurrent run is never reaped mid-flight (default: `120`)
- `BAND_E2E_SCORECARD_JSON`: Write this run's adapter×test scorecard (pass/fail/skip + N/A reasons) as JSON to this path at session end (default: empty = don't emit). CI sets one path per lane; a final job merges them (see `tests/e2e/baseline/scorecard.py` and the Scorecard section of the baseline README)

## Adding a New Framework Integration

When adding a new framework adapter and converter, follow this TDD workflow. Use the lowercase module name (e.g. `openai`, `gemini`) and derive the PascalCase class prefix (e.g. `OpenAI`, `Gemini`).

### Phase 1: Scaffold Source Files

1. Create converter at `src/band/converters/<framework>.py` — class `{Framework}HistoryConverter` with stub `convert()`, `set_agent_name()`, `__init__(*, agent_name=None)`. Use `from band.converters.parsing import parse_tool_call, parse_tool_result`.
2. Create adapter at `src/band/adapters/<framework>.py` — class `{Framework}Adapter` extending `SimpleAdapter[T]` with `__init__` params: `model`, `custom_section`, `enable_execution_reporting`, `history_converter`. Stub `on_message`, `on_started`, `on_cleanup`.
3. If the framework needs an external SDK, add an optional dependency group in `pyproject.toml`.

### Phase 2: Register with Conformance Infrastructure

1. Add an output adapter in `tests/framework_configs/output_adapters.py` — choose base class matching output format (`BaseDictListOutputAdapter`, `StringOutputAdapter`, `SenderDictListAdapter`, or `LangChainOutputAdapter`).
2. Register converter config in `tests/framework_configs/converters.py` — factory function, builder function returning `ConverterConfig` with behavioral flags, append to `_CONVERTER_CONFIG_BUILDERS`.
3. Register adapter config in `tests/framework_configs/adapters.py` — factory function with mocked constructor args, builder function returning `AdapterConfig`, append to `_ADAPTER_CONFIG_BUILDERS`.

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
- Always load and validate `BAND_WS_URL` and `BAND_REST_URL` with `ValueError`
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
  or property with an intent-revealing name. In tests especially, assert on a
  readable projection of the observable outcome, not raw internals — e.g.
  `assert reply.outline == ["tool_call (permission)", "message", ...]` over a
  hand-rolled comprehension pulling `message_type` out of each event dict.
- Prefer a single source of truth for a value or closed vocabulary consumed in more
  than one place: give it one definition — a constant, a `StrEnum`, or a small helper
  — that every site references, rather than re-typing the same magic literal in a
  producer and the consumer that reads it (a typo then fails silently). Keep genuinely
  distinct vocabularies separate, though — don't merge two sets that only happen to
  share some values today (e.g. the ACP `ChunkType` a chunk carries vs. the platform
  `message_type` an event is posted under).
- Comments should describe the code as it is, not narrate what changed between versions.

## Pre-Commit Checklist

```bash
uv run ruff check .
uv run ruff format .
uv run pyrefly check
uv run pytest tests/ --ignore=tests/integration/ --ignore=tests/e2e/ -v
```


## Engineering Rules

### Error Handling

#### Pydantic ValidationError

- Catch `pydantic.ValidationError` separately from generic `Exception`
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

#### Exception Hierarchy

- Use specific exceptions over generic ones
- Create custom exception classes for domain-specific errors
- Always include context in exception messages

#### Error Messages

- Make error messages actionable and clear
- Include relevant context (what failed, why, what to do)
- Avoid exposing internal implementation details to end users

#### Required Configuration

- Use `raise ValueError(...)` for missing required configuration
- Do NOT use `logger.error()` + `sys.exit()` pattern
- Fail fast with clear error messages

Example:
```python notest
# Good
if not api_key:
    raise ValueError("OPENAI_API_KEY environment variable is required")

# Bad
if not api_key:
    logger.error("Missing API key")
    sys.exit(1)
```


### Git Workflow

#### Branch Naming

Branch names should match the Linear issue:

- Format: `<prefix>/<title>-<ISSUE-ID>`
- Example: `feat/add-user-auth-ENG-123`

Prefixes:

- `feat/` - New features
- `fix/` - Bug fixes
- `refactor/` - Code refactoring
- `docs/` - Documentation changes
- `chore/` - Maintenance tasks

##### Creating Branches from Linear Issues

Use `git lb` to create properly named branches from Linear issues:

```bash
git lb INT-84
```

This automatically fetches the issue title from Linear and creates a branch with the correct naming convention.

If `git lb` is not installed, ask the developer for the proper branch name.

#### Commit Messages

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

#### Pull Request Titles

PR titles MUST use conventional commits format:

- `feat:` - New features
- `fix:` - Bug fixes
- `docs:` - Documentation changes

Examples:
- `feat: Add custom tools support to all adapters`
- `fix: Handle validation errors in execute_tool_call`
- `docs: Update README with new adapter examples`

#### Pre-Commit Checklist

- Run tests before committing
- Run linting and formatting
- Ensure type checking passes
- Review changes with `git diff`

#### Code Review

- Keep PRs focused and reasonably sized
- Respond to review comments promptly
- Squash commits when merging if history is messy


### GitHub PR Inline Comments

#### Adding Inline Review Comments

To add inline comments at specific lines in a PR, use the GitHub Reviews API with `gh api`:

```bash
cat << 'EOF' | gh api repos/{owner}/{repo}/pulls/{pr_number}/reviews --method POST --input -
{
  "commit_id": "<commit_sha>",
  "event": "COMMENT",
  "body": "Review summary",
  "comments": [
    {
      "path": "src/path/to/file.py",
      "line": 42,
      "body": "Your comment here"
    }
  ]
}
EOF
```

#### Getting the Correct Line Numbers

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

#### Common Mistakes to Avoid

- **Don't use `gh pr review --comment`** - This adds a general comment, not inline comments
- **Don't use diff line numbers** - Use actual file line numbers from the new version
- **Don't use `-f` flag for JSON arrays** - Pass JSON via stdin with `--input -`
- **Don't guess line numbers** - Always verify by checking the actual file content

#### Example: Full Workflow

```bash
# 1. Get commit SHA
COMMIT=$(gh pr view 83 --json headRefOid -q .headRefOid)

# 2. Find the line number for a specific pattern
curl -s "https://raw.githubusercontent.com/owner/repo/${COMMIT}/src/file.py" | grep -n "def my_function"

# 3. Add inline comment at that line
cat << 'EOF' | gh api repos/owner/repo/pulls/83/reviews --method POST --input -
{
  "commit_id": "abc123...",
  "event": "COMMENT",
  "body": "Code review",
  "comments": [
    {
      "path": "src/file.py",
      "line": 25,
      "body": "Consider renaming this function for clarity"
    }
  ]
}
EOF
```

#### Multiple Comments

Add multiple inline comments in a single review:

```bash
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

#### Review Events

The `event` field can be:
- `"COMMENT"` - Submit general feedback without approval
- `"APPROVE"` - Approve the PR
- `"REQUEST_CHANGES"` - Request changes before merging

