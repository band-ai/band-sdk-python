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
