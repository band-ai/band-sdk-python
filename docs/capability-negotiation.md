# Capability Negotiation

How an adapter declares what it emits and what platform capabilities it
wants, and how those requests get pruned against what the connected Band
deployment actually supports.

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

`Capability.FILES` gates the three file tools (see [Platform
Tools](platform-tools.md)), but declaring it isn't enough by itself: the
platform's room-file storage (`ff_file_transfer`) is an **on-prem-only
deployment flag, off everywhere on SaaS today** — never enable
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
`ACPClientAdapter`, `LettaAdapter` — each builds its MCP tool registration
from `self.features.capabilities` at `__init__` time) overrides it to
rebuild that cache from the negotiated features too — otherwise a
deployment that negotiates FILES *off* would keep serving the file tools
anyway. `SlackAdapter` overrides it to also delegate into the wrapped inner
adapter, since its own `_resolve_features()` only mirrors features into the
inner adapter once, at construction.

**Every registered adapter now declares `Capability.FILES`** —
`claude_sdk`, `anthropic`, `langgraph`, `gemini`, `google_adk`, `agno`,
`strands`, `codex`, `copilot_sdk`, `opencode`, the ACP client adapter,
`letta`, `parlant`, `pydantic_ai`, `crewai`, and `crewai_flow`. The
registry-driven ones (schemas built generically from
`iter_tool_definitions`) needed only the declaration; `parlant`,
`pydantic_ai`, and `crewai`/`crewai_flow` hand-roll one wrapper per
platform tool, so each grew three new hand-written wrappers instead.

Real image vision passthrough for `band_read_room_file` (a small
previewable image reaches the model as actual image content instead of a
`json.dumps`'d text block) is verified for `claude_sdk`, `anthropic`,
`opencode` (and the ACP client adapter / published `band-mcp` CLI, which
share its MCP-engine fix), `gemini`, `langgraph`, `agno`, `strands`,
`copilot_sdk`, `codex`, `pydantic_ai`, and `crewai`/`crewai_flow` — see
`IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS` in
`tests/framework_conformance/test_adapter_conformance.py` for the
up-to-date list and per-adapter mechanism citations. `google_adk` and
`parlant` are confirmed **not** supportable: `google_adk` because
installed google-adk's own tool-response builder drops multimodal content
before it reaches the model (an upstream framework limitation), `parlant`
because its `ToolResult` has no multimodal field and its own MCP
integration discards image content blocks the same way.

`AgentTools.get_tool_schemas`/`get_anthropic_tool_schemas`/
`get_openai_tool_schemas` and `iter_tool_definitions` take a single
`capabilities: frozenset[Capability] | None` parameter — the boolean
`include_memory`/`include_contacts` pair they used to take is gone
(breaking change, no back-compat shim). `None` resolves to the pre-existing
default (contacts only); the hub-room execution path still unions
`Capability.CONTACTS` in regardless of what was requested.
