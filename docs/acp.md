# ACP (Agent Client Protocol) Integration

ACP enables editors (Zed, Cursor, JetBrains, Neovim) to communicate with AI agents via JSON-RPC over stdio. The SDK provides both server and client sides.

## Architecture

Two-layer pattern (mirrors A2A Gateway):

| Layer | Server Side | Client Side |
|-------|-------------|-------------|
| Protocol | `ACPServer` (JSON-RPC handler) | ACP SDK's `spawn_agent_process` |
| Platform Bridge | `BandACPServerAdapter` | `ACPClientAdapter` |

**Server**: Editor -> ACP -> `ACPServer` -> `BandACPServerAdapter` -> Band REST/WS -> Peers
**Client**: Band room message -> `ACPClientAdapter` -> stdio subprocess **or** TCP connection (Codex, Claude Code, Cursor, GitHub Copilot, etc.)

## Key Files

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

## CLI

```bash
# Installed via pip/uv as console_scripts entry point
band-acp --agent-id my-agent --api-key $BAND_API_KEY

# Or with environment variables
BAND_AGENT_ID=my-agent BAND_API_KEY=key band-acp
```

## Session Lifecycle

1. Editor connects via stdio -> `ACPServer.on_connect()` stores client ref
2. `new_session(cwd, mcp_servers)` -> creates Band room, stores cwd/mcp_servers per session
3. `prompt(blocks, session_id)` -> extracts text/image/resource content, sends to room, waits for `done_event`
4. `on_message()` receives peer response -> `EventConverter.convert()` -> `session_update` back to editor
5. `on_cleanup(room_id)` -> removes all session state, unblocks pending prompts

## Live, causally-ordered emission (Client Adapter)

A turn's events must land in the room in the order they happened, because two things post **live, mid-turn**: a Band messaging tool's own room post (a remote/injected band-mcp calling REST as it runs), and a denied-permission pair. So `ACPCollectingClient` doesn't buffer-then-flush — it **streams** finalized chunks to a per-session live sink (`set_sink`) as `session_update`s arrive:

- Consecutive text/thought deltas coalesce into one run, finalized at the next boundary or the turn-end `flush`.
- A call's `tool_call_update` frames fold by `tool_call_id` into one result, finalized once the call reports a terminal status (`completed`/`failed`).
- The buffer (`_session_chunks`) still accumulates the finalized chunks — the per-turn record `get_collected_chunks` returns, cleared each turn by `reset_session` (in-memory, not durable) and keyed per session so concurrent rooms don't need a global lock.

`RoomTurnEmitter` (`room_emitter.py`) is the sink: it posts narration (thought/tool_call/tool_result/plan) live for **every** tool call — including Band messaging tools, with no suppression — and holds **only** the assistant text until close (the text-fallback decision needs the whole turn). `ACPRuntime.prompt(..., on_chunk=emitter.emit)` registers the sink and `flush`es at turn end.

## History replay fallback (Client Adapter)

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

## Reply Delivery (Client Adapter)

Tool-first with a text fallback, matching `copilot_sdk`/`codex`: if the turn posted via a Band messaging tool, the agent's plain text is **not** also relayed; otherwise the held text is relayed at turn close. The decision lives in `turn_replied_in_room()` (`room_emitter.py`), which reads the collected tool-call stream — the ACP adapter can't flip an in-process flag like the siblings, because its tools may execute out-of-process (remote band-mcp), so it matches `tool_call` title + `completed` status. Which tools count is defined once in `is_room_posting_tool()` / `ROOM_POSTING_TOOL_NAMES` (`src/band/runtime/tools.py`): the SDK's `band_send_message` (also what band-mcp 1.3.2+ advertises, since its registrar reuses the SDK tool definitions) plus the legacy `create_agent_chat_message` spelling from band-mcp ≤1.3.1. This suppression is about the text fallback only — the call's own `tool_call`/`tool_result` narration (below) is never suppressed.

## Tool narration (Client Adapter)

Every tool call is narrated as `tool_call`/`tool_result`, including Band messaging tools (`band_send_message`/`band_send_event`) — there is no "self-reporting" special case. Because emission is live and causally ordered (above), a Band messaging tool's own room post lands *between* its `tool_call` and `tool_result` narration, so the room naturally reads `tool_call -> message -> tool_result` without any special-casing.

Narrated names are canonical: an ACP runtime that prefixes MCP tool names (Copilot registers the loopback server's tools as `band-<tool>`) has the prefix stripped at chunk construction when the name reveals one of the adapter's own registered tools (`canonicalize_mcp_tool_name` in `src/band/runtime/tools.py`, sharing one resolver with `is_room_posting_tool`). Foreign tool names pass through untouched.

## Capabilities (Client Adapter)

`ACPClientAdapter` supports `Capability.MEMORY` and `Capability.CONTACTS`. Only memory tools are gated on the declared capability (an enterprise feature the adapter must opt into); contact tools register unconditionally, matching the adapter's pre-existing default that every caller without `features=` (every ACP example) relies on — declaring `Capability.CONTACTS` only stops the base class's unsupported-capability warning for a caller that does declare it. The registered tool vocabulary (computed once at construction) drives tool-name canonicalization too. `render_system_prompt` carries the matching capability sections.

## Permission pairing (Client Adapter)

Auto-approval grants silently — no event posts for an approved request, ordinary or Band tool alike; the call's real `tool_call`/`tool_result` narration (above) is the visible record. Only a **denied** request posts a synthetic `tool_call`/`tool_result` pair (`RoomTurnEmitter.open_permission`), since the tool never runs and there is nothing else to show it happened.

## Optional Dependency

```toml
[project.optional-dependencies]
acp = ["agent-client-protocol"]
```

Install with: `pip install band-sdk[acp]` or `uv add band-sdk[acp]`

## Client transports (stdio / TCP)

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

## GitHub Copilot CLI backend

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
