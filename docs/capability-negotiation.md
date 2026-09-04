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
  `Capability.MEMORY`/`Capability.CONTACTS`/`Capability.FILES`/`Capability.TASKS`
  puts extra tool schemas in front of the model on every turn, so it stays off
  by default.
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

Only `Capability.FILES` has an entry today. `Capability.MEMORY`,
`Capability.CONTACTS`, and `Capability.TASKS` have none: the real
Fern-generated `AgentMe.feature_flags` field (`band_rest.types.AgentMe`)
currently documents only `ff_file_transfer`, so those three capabilities are
always advertised regardless of deployment. Add an entry here only once the
platform actually ships a corresponding `ff_*` flag for one of them — never
invent a flag key ahead of the platform, since a name with no matching key in
`feature_flags` prunes the capability everywhere (a missing key reads as
unsupported, per the rule above).

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

Every registered adapter declares `Capability.FILES`. Registry-driven
adapters (schemas built generically from `iter_tool_definitions`) need
only the declaration; `parlant`, `pydantic_ai`, and `crewai`/`crewai_flow`
hand-roll one wrapper per platform tool, so each carries its own
hand-written wrapper for the three file tools.

Real image vision passthrough for `band_read_room_file` (a small
previewable image reaches the model as actual image content instead of a
`json.dumps`'d text block) is supported by every adapter except
`google_adk` and `parlant`, which cannot carry multimodal tool-result
content at all: `google_adk`'s own tool-response builder drops it before
it reaches the model (an upstream framework limitation), `parlant`'s
`ToolResult` has no multimodal field and its own MCP integration discards
image content blocks the same way. `letta` and `copilot_acp` (which wraps
the ACP client adapter) get the fix transitively — both route tool
execution through the shared MCP engine rather than calling
`execute_tool_call` directly — so they have no adapter-local code path to
probe in isolation, but are not excluded from support. See
`IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS`/`IMAGE_PASSTHROUGH_EXCLUSIONS`
in `tests/framework_conformance/test_adapter_conformance.py` for the
enforced, always-current per-adapter list and mechanism citations.

`AgentTools.get_tool_schemas`/`get_anthropic_tool_schemas`/
`get_openai_tool_schemas` and `iter_tool_definitions` take a single
`capabilities: frozenset[Capability] | None` parameter. `None` resolves to
contacts-only; the hub-room execution path always unions
`Capability.CONTACTS` in regardless of what was requested.
