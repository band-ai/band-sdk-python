# ACP (Agent Client Protocol) Integration

ACP enables editors (Zed, Cursor, JetBrains, Neovim) to communicate with AI agents via JSON-RPC over stdio. The SDK provides both server and client sides.

## Architecture

Two-layer pattern (mirrors A2A Gateway):

| Layer | Server Side | Client Side |
|-------|-------------|-------------|
| Protocol | `ACPServer` (JSON-RPC handler) | ACP SDK's `spawn_agent_process` |
| Platform Bridge | `BandACPServerAdapter` | `ACPClientAdapter` |

**Server**: Editor -> ACP -> `ACPServer` -> `BandACPServerAdapter` -> Band REST/WS -> Peers
**Client**: Band room message -> `ACPClientAdapter` -> its room-owned stdio subprocess (Codex, Claude Code, Cursor, GitHub Copilot, etc.)

## Key Files

| File | Purpose |
|------|---------|
| `src/band/integrations/acp/server.py` | `ACPServer` — handles ACP JSON-RPC methods, does not subclass `acp.Agent`; `run_acp_server` — runs it with `use_unstable_protocol` (required for `session/fork`, `session/resume`, `session/close`) |
| `src/band/integrations/acp/server_adapter.py` | `BandACPServerAdapter` — REST client, room/session mapping |
| `src/band/integrations/acp/client_adapter.py` | `ACPClientAdapter` — drives a room-owned ACP agent over stdio |
| `src/band/integrations/acp/client_runtime.py` | `ACPRuntime` (room-owned stdio lifecycle) + `ACPCollectingClient` (session_update parsing / coalescing / collapse / live sink) |
| `src/band/integrations/acp/room_emitter.py` | `RoomTurnEmitter` — posts a turn's chunks to the room in causal order; `turn_replied_in_room` (text-fallback suppression) |
| `src/band/adapters/copilot_acp.py` | `CopilotACPAdapter` — thin `ACPClientAdapter` for the GitHub Copilot CLI |
| `src/band/adapters/cursor_acp.py` | `CursorACPAdapter` — Cursor CLI backend with room-routed decisions |
| `src/band/adapters/omp_acp.py` | `OmpACPAdapter` — stdio-only OMP (`omp acp`) with enforced `always-ask` approval |
| `src/band/adapters/kiro_acp.py` | `KiroACPAdapter` — thin `ACPClientAdapter` for AWS Kiro CLI |
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

Tool-first with a text fallback, matching `copilot_sdk`/`codex`: if the turn posted via a Band messaging tool, the agent's plain text is **not** also relayed; otherwise the held text is relayed at turn close. The decision lives in `turn_replied_in_room()` (`room_emitter.py`), which reads the collected tool-call stream — the ACP adapter can't flip an in-process flag like the siblings, because its tools may execute out-of-process (remote band-mcp), so it matches `tool_call` title + `completed` status. Which tools count is defined once in `is_room_posting_tool()` / `ROOM_POSTING_TOOL_NAMES` (`src/band/runtime/tools/registry.py`): the SDK's `band_send_message` (also what band-mcp 1.3.2+ advertises, since its registrar reuses the SDK tool definitions) plus the legacy `create_agent_chat_message` spelling from band-mcp ≤1.3.1. This suppression is about the text fallback only — the call's own `tool_call`/`tool_result` narration (below) is never suppressed.

## Tool narration (Client Adapter)

Every tool call is narrated as `tool_call`/`tool_result`, including Band messaging tools (`band_send_message`/`band_send_event`) — there is no "self-reporting" special case. Because emission is live and causally ordered (above), a Band messaging tool's own room post lands *between* its `tool_call` and `tool_result` narration, so the room naturally reads `tool_call -> message -> tool_result` without any special-casing.

Narrated names are canonical: an ACP runtime that prefixes MCP tool names (Copilot registers the loopback server's tools as `band-<tool>`) has the prefix stripped at chunk construction when the name reveals one of the adapter's own registered tools (`canonicalize_mcp_tool_name` in `src/band/runtime/tools/registry.py`, sharing one resolver with `is_room_posting_tool`). Foreign tool names pass through untouched.

## Capabilities (Client Adapter)

`ACPClientAdapter` supports `Capability.MEMORY` and `Capability.CONTACTS`. Only memory tools are gated on the declared capability (an enterprise feature the adapter must opt into); contact tools register unconditionally, matching the adapter's pre-existing default that every caller without `features=` (every ACP example) relies on — declaring `Capability.CONTACTS` only stops the base class's unsupported-capability warning for a caller that does declare it. The registered tool vocabulary (computed once at construction) drives tool-name canonicalization too. `render_system_prompt` carries the matching capability sections.

## Permission pairing (Client Adapter)

Auto-approval grants silently — no event posts for an approved request, ordinary or Band tool alike; the call's real `tool_call`/`tool_result` narration (above) is the visible record. Only a **denied** request posts a synthetic `tool_call`/`tool_result` pair (`RoomTurnEmitter.open_permission`), since the tool never runs and there is nothing else to show it happened.

## Dynamic model and reasoning configuration (Client Adapter)

Remote ACP agents can advertise a live `configOptions` catalog for each session. Pass
an async `resolve_session_config` callback to choose from the agent's actual select
options; this supports model lists, reasoning effort, and future provider-specific
selectors without a Band-maintained vocabulary.

The behavior is owned by `ACPClientAdapter`, so every transport and profile gets it:
stdio, TCP, custom transports, and `CopilotACPAdapter` through its
`CopilotACPAdapterConfig.resolve_session_config` field. The in-process ACP test
harness implements the same wire method, keeping the protocol path proven without
provider-specific test doubles.

[`examples/acp/clients/generic.py`](../examples/acp/clients/generic.py) is a
runnable bridge example. Leave `ACP_MODEL` and `ACP_REASONING_EFFORT` unset to
log each session's advertised values, then set one to an exact advertised value.
The example ignores unavailable values and makes only one selection per new
session: selecting a model can replace the effort catalog.

The callback is called once after each new or restored session is established and
before its first prompt. Selections apply in mapping order. Each successful
`session/set_config_option` response replaces the catalog used to validate the next
selection, because choosing a model can change the available reasoning levels. An
invalid selection, rejection, timeout, or malformed `session/set_config_option`
response fails that room turn visibly instead of silently falling back to a different
setting.

## Cursor CLI backend

`CursorACPAdapter` starts Cursor with `agent acp` and authenticates through the
standard `cursor_login` method. Its `resolve_session_config` callback receives
Cursor's live ACP catalog, so model, embedded reasoning effort, mode, and future
provider options stay provider-defined rather than being copied into the SDK.
The Cursor example logs that published catalog and selects `CURSOR_MODEL` and
`CURSOR_REASONING_EFFORT` only when the active session advertises those values.

Cursor's question, plan, and permission requests can be manual or automatic.
Manual is the default: the adapter posts a tokenized room prompt, and an authorized
participant resolves it with `/cursor answer <token>`, `/cursor accept <token>` or
`/cursor reject <token>`, or `/cursor select <token>` or `/cursor deny <token>`.
`CursorACPAdapterConfig` controls the
automatic policies, timeout, capacity, and optional sender allowlist. Cursor omits
session identifiers from its extension notifications, so its adapter serializes
extension-capable turns and binds todo, task, and image notifications to that turn.

For a noninteractive deployment, provide `CURSOR_API_KEY` or `CURSOR_AUTH_TOKEN`
to the subprocess; otherwise authenticate the CLI with `agent login`. See the
[Cursor ACP documentation](https://cursor.com/docs/cli/acp) for the provider's
wire contract and [the Cursor example](../examples/acp/clients/cursor.py) for the
SDK entry point.

## Optional Dependency

```toml
[project.optional-dependencies]
acp = ["agent-client-protocol"]
```

Install with: `pip install band-sdk[acp]` or `uv add band-sdk[acp]`

## Client workspace isolation

`ACPClientAdapter` creates an isolated `./.band-workspaces/<room-id>` directory for
each Band room by default. Pass `workspace_for_room` only to select a different
absolute workspace policy. It lazily starts one stdio agent process per room and
stops that process when the room is cleaned up. TCP and custom transport injection are
rejected because they cannot prove that a remote process belongs to only one room.
The assigned working directory is not an operating-system sandbox; configure the agent's
sandbox policy separately when that boundary is required. A custom resolver must assign a
different workspace to every live room, and the adapter requires a non-empty stdio command.

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
- Remote Copilot ACP over TCP is not supported by this adapter because one remote
  process cannot be proven to be owned by one Band room.
- Copilot in a Docker **microVM sandbox** ([`sbx`](https://docs.docker.com/ai/sandboxes/))
  over stdio (`sbx exec -i <sandbox> copilot --acp`): `examples/acp/copilot_sandbox/` —
  isolation + a host-side secret proxy (token never enters the VM). Uses the ordinary
  stdio transport; auth is out-of-band via `sbx secret set -g github`.

## OMP (oh-my-pi) ACP backend

`OmpACPAdapter` (`src/band/adapters/omp_acp.py`) drives OMP's native `omp acp`
stdio server through `ACPClientAdapter`. The spawn command always ends with
`--approval-mode always-ask` (overlays / global config cannot widen approvals),
and the adapter advertises only form-elicitation client capabilities — never
filesystem or terminal. Provider credentials are passed only via the child
`env` (see `omp_provider_env` in `band.integrations.omp`); do not log keys.

Registered in the baseline matrix under the `backends` lane, gated on
`Dep.OMP` (Bun >= 1.3.14, a working `omp` / `omp acp`, and the provider API key
for the selected `OMP_MODEL`). Each matrix spawn uses a disposable cwd and a
fresh `PI_CODING_AGENT_DIR`. Excluded from framework-conformance as a bridge.

- Example: `examples/acp/clients/omp.py`.
- Pin used by CI: `@oh-my-pi/pi-coding-agent@18.2.8` (see `.github/scripts/setup-omp.sh`).

## AWS Kiro CLI backend

`KiroACPAdapter` (`src/band/adapters/kiro_acp.py`) drives `kiro-cli acp` through
`ACPClientAdapter`, stdio only — Kiro's ACP mode has no documented remote/`--port`
option the way Copilot's does. `KiroACPClientProfile`
(`src/band/integrations/acp/client_profiles.py`) handles Kiro's experimental
`_kiro.dev/*` extension methods: declines `_kiro.dev/mcp/oauth_request` (no OAuth UI
is wired up for a headless agent) and renders `_kiro.dev/metadata`'s context-window
usage as a room-visible plan chunk when the payload matches a recognized shape,
dropping it otherwise (Kiro's own docs mark these extensions experimental and
versioned).

Auth is `kiro-cli login` (AWS Builder ID / Identity Center / Google / GitHub social
OAuth — browser or device-flow only, no non-interactive flag) or the documented
`KIRO_API_KEY` env var for headless/CI-CD use; pass it via the config `env`, unset to
use the ambient login. `KIRO_API_KEY` requires a paid Kiro Pro/Pro+/Pro Max/Power
subscription (https://kiro.dev/docs/cli/headless/) — free-tier auth (AWS Builder ID /
Google / GitHub) is interactive-only and cannot authenticate headless. Unlike
Copilot, Kiro also has no BYOK/provider-swap to route around this (verified against
`kiro-cli`'s own binary and the TS sibling's adapter/tests/examples; it remains an
open, unimplemented feature request upstream).

**No live E2E coverage**: this org has decided not to purchase a Kiro subscription,
so the baseline matrix builder (`_build_kiro_acp` in
`tests/e2e/baseline/toolkit/builders.py`) is registered `e2e_pending` — it keeps the
adapter discoverable and placed in the `backends` CI lane, but runs zero live matrix
cells (unlike Copilot's BYOK fallback, there's no hermetic cell this adapter can ever
run without a paid key). The best available coverage without one:

- Unit tests: `tests/adapters/test_kiro_acp_adapter.py` (construction/config) and
  `tests/integrations/acp/test_client_adapter.py`'s
  `TestACPCollectingClientKiroProfileExtensions` / `TestResolveACPClientProfile`
  (the `KiroACPClientProfile` object in isolation, including malformed/out-of-range
  usage-payload edge cases).
- Wire-level tests: `tests/integrations/acp/test_client_adapter_behavior.py`'s
  `TestKiroACPClientProfileOverTheWire` drives `KiroACPClientProfile` through a real
  ACP connection to a scripted `FakeACPAgent` (a real socketpair, real JSON-RPC
  framing — only the LLM and the `kiro-cli` binary are faked), proving the
  `_kiro.dev/mcp/oauth_request` decline and `_kiro.dev/metadata` context-window
  notification are actually wired end-to-end when Kiro is the active profile.
  `TestKiroMultiStageSessionRecall` goes further: two real, sequential adapter
  lifecycles against the same `FakeACPAgent` (simulating a `kiro-cli` restart),
  proving both the native `session/load` resume path and the room-replay fallback
  path — the same two scenarios the deleted live E2E smoke covered.
- Excluded from framework-conformance as a bridge (shares `ACPClientAdapter`'s MCP
  engine fix, so it has no separate probe of its own).

None of this proves `kiro-cli acp`'s real wire behavior (session/load semantics,
genuine tool-call shapes, actual auth handshake) — only that Band's side of the
bridge is correct against the ACP protocol as documented. Revisit `e2e_pending` if
the subscription decision changes; `tests/e2e/baseline/smoke/adapters/test_kiro_acp.py`
existed at one point with the same two recall scenarios copilot_acp's live smoke
covers (room-replay fallback and native `session/load` resume) and can be restored
from history as a starting point.

**Known residual risk — per-turn usage may be misreported if Kiro reports
cumulative totals**: see `ACPClientAdapter._turn_usage`'s docstring for why,
and why kiro_acp specifically (unlike the other live ACP vendors) has no
guard against it.
