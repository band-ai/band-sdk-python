# Migrating to Band SDK v2.0

## Architecture mental model

Hold this story; everything else is private machinery:

```text
Host / transport → Agent → adapter.handle_turn(inp) → Tools (+ delivery)
```

The adapter type is the `FrameworkAdapter` contract. `SimpleAdapter` is
the base class most adapters extend (`handle_turn` → history convert →
`on_message`).

Adapters share one contract (`SimpleAdapter`) and fall into three kinds by
where the model loop lives:

| Kind | Loop lives in | Examples |
|------|---------------|----------|
| Framework | Their SDK | LangGraph, CrewAI, Pydantic AI, … |
| Native | Band + `ModelProvider` | Anthropic, Gemini |
| Bridge | Remote agent / CLI | ACP client, A2A, OpenCode, Codex, Copilot* |

Gateways (`SlackGateway`, `ACPGateway`, `A2AGateway`) are **hosts** — they
own agent lifecycle and an inbound transport. They are not a fourth adapter
kind. `HistoryConverter`, `ObservingTools`, and `NativeToolLoopBackend` stay
behind the Adapter; do not treat them as peer architecture layers.
`NativeProviderAdapter` is a private helper shared by Anthropic/Gemini only —
not a public base class and not a fourth kind.

This guide accumulates old → new mappings as each redesign phase lands.
v2.0 is a breaking release: removed constructor aliases raise `TypeError`
(or fail dataclass construction) instead of emitting `BandDeprecationWarning`.

## Removed compatibility shims

The following deprecated paths were removed in v2.0:

- Claude SDK `BAND_TOOLS` aliases were removed. Use
  `band.adapters.claude_sdk.BAND_ALL_TOOLS` for the complete surface or
  `band.integrations.claude_sdk.tools.BAND_CHAT_TOOLS` for chat tools.
- Parlant's `set_current_tools` / `get_current_tools` aliases were removed. Use
  the session-keyed `set_session_tools` / `get_session_tools` APIs.
- On Anthropic / Gemini / PydanticAI, bare `(InputModel, handler)` entries are
  no longer accepted in `additional_tools`; use
  `FunctionTool.from_custom_tool_def(...)`, a `FunctionTool`, or an
  `@tool`-decorated callable. ClaudeSDK / CrewAI / GoogleADK / Codex still
  accept `CustomToolDef` tuples.
- Runtime tool descriptions require canonical `band_`-prefixed names;
  unprefixed names now use the unknown-tool fallback.
- Mention dictionaries must contain an `id`. For unresolved mentions, pass
  participant handles as `list[str]`.
- `BackendCapabilities` and `AgentBackend.capabilities` were removed. Both
  shipped backends declared the same three flags and nothing ever branched on
  them, so the type promised a contract that nothing enforced. A backend that
  cannot do something should say so where it is asked to do it.
- `band.core.run.context.SimpleModelContext` was removed; use
  `ProviderModelContext`, which is the same one-field `ModelContext`.
- `RunResult.output` and its four-arm union (`TextOutput`, `StructuredOutput`,
  `ContentBlocksOutput`, `EmptyOutput`) were replaced by `RunResult.text:
  str | None`. Only the text and empty arms were ever produced. `None` means
  the turn produced no assistant text; `""` is a real empty reply.
- `RunContext.deadline` was removed. It was advisory and no backend enforced
  it; a backend that bounds a turn does so itself (as OpenCode's
  `turn_timeout_s` does).
- `InstructionPolicy.compose()` / `.flatten()`, `ComposedLayer`,
  `InstructionLayer` and `resolve_instructions` were removed. `compose` built
  a tuple its only caller flattened back to a string one line later, and
  `resolve_instructions` returned its argument unchanged. Use
  `InstructionPolicy.render(...)`, which is unchanged, and pass
  `instructions=` straight through. `Instruction` / `InstructionMode`
  (including `PREPEND`) are unaffected.
- `InstructionPolicy.render(run_instructions=...)` was removed. No caller ever
  supplied it, so the run layer never rendered.
- `RunRequest` was removed, along with the `AgentInput` ↔ `RunRequest`
  mapping. Turns pass `AgentInput` straight into `FrameworkAdapter.handle_turn` —
  the old `RunRequest` held the same fields under four different names.
  `NativeToolLoopBackend` is an internal tool loop (not an adapter): it owns
  its session, so it takes `session_id=`, `message=` and the two context
  strings rather than an input whose history it would ignore. `SessionHistoryPolicy.prime_turn` takes the same three values.
- `DeliveryReceipt.outcome` and `DeliveryReceipt.tool_call_id` were removed.
  `outcome` had exactly one legal value — its own validator rejected the rest
  — and nothing read the call id. A receipt is evidence that a room post
  succeeded; `tool_name` carries that.
- `SimpleAdapterBackend` and `AgentBackend` were removed. `Agent`, `run_oneshot_turn`, and `AgentStream.observe` take a `FrameworkAdapter` only. The ObservingTools wrap (delivery + turn sink) lives in `run_adapter_turn`. The turn entrypoint on the contract is `handle_turn` (formerly `on_event` on the adapter protocol — not to be confused with platform `Execution.on_event`). Native Anthropic/Gemini adapters compose a private `NativeToolLoopBackend`; tests that need a bare loop use a small `SimpleAdapter` stand-in (`NativeLoopAdapter` in the test helpers).


## Breaking: features-only

Adapter capabilities and event emission are configured only through
`features=AdapterFeatures(...)` in v2.0. The deprecated boolean flags were removed:

| Removed | Replacement |
|---|---|
| `enable_memory_tools=True` | `features=AdapterFeatures(capabilities={Capability.MEMORY})` |
| `enable_execution_reporting=True` | `features=AdapterFeatures(emit={Emit.EXECUTION})` |
| Claude SDK `enable_execution_reporting=True` | `features=AdapterFeatures(emit={Emit.EXECUTION, Emit.THOUGHTS})` |
| Codex `emit_thought_events=True` | `features=AdapterFeatures(emit={Emit.THOUGHTS})` |
| `enable_task_events=True` | `features=AdapterFeatures(emit={Emit.TASK_EVENTS})` |

Codex, OpenCode, and Letta still default to `Emit.TASK_EVENTS` when `features` is
omitted. Passing an explicit `features=` value, including an empty one, is honored
as-is.

## Phase 1 — Instruction unification

One parameter: `instructions=`. A bare `str` means **APPEND**. Use
`Instruction(..., mode=...)` for **PREPEND** or **REPLACE**.
The Phase 1 aliases below were removed in this breaking change.

| Old parameter | Adapters | Old semantics | New spelling |
|---|---|---|---|
| `system_prompt=` | anthropic, gemini, pydantic_ai, google_adk | **REPLACE** entire rendered prompt (skips identity, base, capabilities) | `instructions=Instruction("...", mode=InstructionMode.REPLACE)` |
| `prompt=` | anthropic, gemini | **APPEND** via developer section | `instructions="..."` |
| `custom_section=` | anthropic, gemini, pydantic_ai, google_adk, claude_sdk | **APPEND** | `instructions="..."` |
| `include_base_instructions=` | anthropic, gemini | Toggles SDK Base layer | **Retained** (unchanged) |

```python
from band import Instruction, InstructionMode

# APPEND (default)
AnthropicAdapter(instructions="Focus on Python and pandas.")

# REPLACE (equivalent to the removed system_prompt= semantics)
AnthropicAdapter(
    instructions=Instruction(
        text="You are a terse calculator. Reply with numbers only.",
        mode=InstructionMode.REPLACE,
    )
)

# Each removed spelling now fails, naming what to use instead.
for removed in ("system_prompt", "prompt", "custom_section"):
    try:
        AnthropicAdapter(**{removed: "You are terse."})
    except TypeError as error:
        assert f"{removed}= was removed" in str(error)
        assert "instructions=" in str(error)
    else:
        raise AssertionError(f"{removed}= should no longer be accepted")
```

**Rules**

- `system_prompt=`, `prompt=`, and `custom_section=` are no longer accepted by
  the Phase 1 adapters.
- CrewAI: do **not** map `instructions=` to `backstory`; `role` / `goal` /
  `backstory` stay native. CrewAI's old `system_prompt=`→`backstory` alias was
  also removed; CrewAI's canonical `custom_section=` remains available.
- LangGraph: out of scope for this axis (compiled-graph wrapper).
- Composition is owned by `InstructionPolicy.render(...)`: the SDK base layer
  first, then the agent's own instructions, combined per their mode.


## Phase 2 — Provider split & common options

`AnthropicAdapter` / `GeminiAdapter` remain the public constructors (Native
kind). Each is an ordinary `SimpleAdapter` that *composes* a provider
(`AnthropicProvider` / `GeminiProvider`) with a private tool loop
(`NativeToolLoopBackend`) — that loop is not an `AgentBackend` and not something
`Agent` takes. LLM calls go through the provider; session history, tool rounds,
and `TurnUsage` aggregation go through the loop. A private helper
(`NativeProviderAdapter`) shares their `on_message` body; it is not a public
architecture tier.

The v1 `_call_anthropic` / `_call_gemini` methods are gone. The loop calls
`provider.complete()` directly, so a completion is projected once instead of
round-tripping through the adapter's native message shape and back. To drive a
turn deterministically, replace the SDK client the provider owns — the adapter
exposes it as `.client`. The snippet below calls `on_message` directly (the
subclass hook); production goes through `adapter.handle_turn(inp)`:

```python fixture:scripted_anthropic_adapter fixture:room_tools fixture:turn_input
adapter, client = scripted_anthropic_adapter("Hello!")

await adapter.on_started("TestBot", "A test bot")
await adapter.on_message(
    turn_input.msg,
    room_tools,
    [],
    None,
    None,
    is_session_bootstrap=True,
    room_id=turn_input.room_id,
)

# The whole turn ran through the provider, so the payload is what a real
# call would have sent.
assert client.last_payload["system"] == adapter._system_prompt
```

### Sampling knobs

| Old | New |
|-----|-----|
| `AnthropicAdapter(max_tokens=…)` | `max_output_tokens=…` (`max_tokens=` is removed) |
| *(none on Anthropic)* | `temperature=` |
| `GeminiAdapter(max_output_tokens=…, temperature=…)` | unchanged (canonical) |

Precedence for a completion: **per-request `ModelRequest.sampling` > provider/adapter instance defaults > provider wire default** (Anthropic always sends `max_tokens`, default `4096`).

### Provider keys

| Removed aliases | Replacement |
|-----------------|-------------|
| `AnthropicAdapter(api_key=…)`, `AnthropicAdapter(anthropic_api_key=…)` | `provider_key=…` |
| `GeminiAdapter(api_key=…)`, `GeminiAdapter(gemini_api_key=…)` | `provider_key=…` |
| `LettaAdapterConfig(api_key=…)` | `provider_key=…` |

These aliases are removed in v2; they no longer warn and forward the value.

### Provider options & escape hatch

```python
from band import AnthropicProvider, UNSET

provider = AnthropicProvider(
    model="claude-sonnet-4-5-20250929",
    max_output_tokens=2048,
    temperature=0.2,
    top_p=0.9,
    raw_options={"metadata": {"user_id": "demo"}},
)
assert provider.sampling.max_output_tokens == 2048
assert provider.sampling.temperature == 0.2
assert UNSET is not None
```

- Unknown `**provider_options` keys → `UnsupportedOptionError`
- `raw_options` may not set credentials/client/transport (`api_key`, `client`, …),
  nor `stream`: these providers always read a whole response, so accepting it
  would do nothing
- A `raw_options` key that collides with an **explicitly set** named option → `TypeError`

### Provider-owned history policy

`NativeToolLoopBackend` resolves session history shape as:

1. Explicit ``history_policy=`` argument (tests / advanced overrides)
2. ``provider.default_history_policy()`` (`AnthropicHistoryPolicy` /
   `GeminiHistoryPolicy`)
3. ``DefaultHistoryPolicy``

Adapters no longer pass ``history_policy=`` when constructing the private
``NativeToolLoopBackend`` — the provider owns that pairing. A custom
``ModelProvider`` should implement ``default_history_policy()`` if it expects a
non-default session shape.

### Per-session turn usage

| Old | New |
|-----|-----|
| ``loop.last_turn_usage`` (property) | ``loop.last_turn_usage(session_id)`` |

One native tool loop serves every room the agent is in and their turns
interleave, so a single "most recent turn" tally reported whichever room last
called the model. The tally is keyed by session and cleared with it.


### One body for tool narration

| Old | New |
|-----|-----|
| ACP `tool_call` content: the tool title (`"band_send_message"`) | `{"name", "args", "tool_call_id"}`, as every other adapter posts |
| ACP `tool_result` content: the raw output text | `{"name", "output", "tool_call_id", "is_error"}` |
| Claude SDK: no `tool_result` event at all | the result its call produced, same body |

A room's `tool_call` / `tool_result` events carry their detail as a JSON body,
and readers parse one shape whatever produced it. ACP posted a bare title
instead, so an ACP-backed agent's tools were unreadable to anything expecting
that shape; Claude SDK looked for results on the assistant turn, where the SDK
never puts them, and so posted none. Both now build the body through
`band.runtime.narration`.

Reading a transcript? Parse the JSON body — `json.loads(event.content)["name"]`
— rather than the event text.

```python
from band.runtime.narration import tool_call_content, tool_result_content

assert tool_call_content("band_send_message", args={"content": "hi"}) == (
    '{"name": "band_send_message", "args": {"content": "hi"}, "tool_call_id": null}'
)
# ``is_error`` is omitted when unknown — absent is not "succeeded".
assert "is_error" not in tool_result_content("band_send_message", output="ok")
```


## Phase 3B — Typed streaming (observation mode)

`AgentStream` is a runtime-owned async view over the turn event sink. Prefer
`async with` (or `await stream.aclose()`) for deterministic cancellation —
bare `async for` is best-effort.

```python fixture:turn_adapter fixture:turn_input fixture:room_tools
from band import AgentStream

stream = AgentStream.observe(turn_adapter, turn_input, tools=room_tools)
async with stream:
    observed = [(envelope.sequence, envelope.event.kind) async for envelope in stream]

# The turn's model posted to the room, so its tool round is what the
# observer sees — in order, numbered from one.
assert observed == [(1, "tool_call"), (2, "tool_result")]
```

Failure separation:

| Failure | Behaviour |
|---------|-----------|
| Model / execution | Yields `RunFailedEvent` (structured `RunFailed`); stream ends normally |
| Transport (`BandConnectionError`) | Raises `StreamError` — aborts iteration |

ACP client turns dual-write onto the same sink: the adapter turn
binds `context.events`, and `RoomTurnEmitter` maps finalized chunks (plus
denied-permission pairs and turn failures) to `TurnEvent`s. So
`AgentStream.observe(acp_adapter, …)` sees the ACP turn
end-to-end without a second execution path.

## Phase 3A — FunctionTool / `@tool`

Portable custom tools use :class:`FunctionTool`, built with the ``@tool`` decorator
or explicitly converted from a ``CustomToolDef`` tuple.

| Old spelling | New spelling |
|---|---|
| ``(InputModel, handler)`` tuple in ``additional_tools`` on Anthropic/Gemini/PydanticAI | ``@tool`` / ``FunctionTool.from_custom_tool_def(...)`` (ClaudeSDK/CrewAI/GoogleADK/Codex still take tuples) |
| Hand-built OpenAI/Anthropic schemas from raw Pydantic models | ``FunctionTool.spec()`` + ``tool_spec_to_openai_schema`` / ``tool_spec_to_anthropic_schema`` |

```python
from band import tool

@tool
async def weather(city: str) -> str:
    """Get weather for a city."""
    return f"Sunny in {city}"

@tool(name="calc", terminal=True)
def calc(x: int) -> int:
    """Add one."""
    return x + 1

AnthropicAdapter(
    additional_tools=[weather, calc],  # FunctionTool | Callable
)

# The decorator names the tool and carries terminal through to the adapter.
assert weather.__band_function_tool__.name == "weather"
assert calc.__band_function_tool__.name == "calc"
assert calc.__band_function_tool__.terminal is True
```

**Rules**

- Bare ``CustomToolDef`` tuples no longer normalize at adapter init. Convert
  explicitly with ``FunctionTool.from_custom_tool_def(...)`` when needed.
- Duplicate tool names in ``additional_tools`` raise ``DuplicateToolError``.
- ``ToolContext`` as the first parameter (or a ``ctx: ToolContext`` parameter) is
  injected at execution time and excluded from the JSON schema.
- ``terminal=True`` sets ``band_terminal`` on the handler wrapper used by adapters.
- Strict schema building (default) raises ``ValueError`` on unsupported parameter
  annotations; ``lenient=True`` skips unsupported params with ``UserWarning``.
- Adapters whose framework builds its own tool schemas (pydantic-ai) pass plain
  callables through untouched, so a type Band's schema builder does not model is
  not an error there. An ``@tool``-decorated callable always keeps the
  ``FunctionTool`` it was decorated with, on every adapter.




### Typed ``gateway.adapter``

`GatewayBase` is parameterized as ``GatewayBase[SomeAdapter]``. Subclasses declare
the adapter once (`ACPGateway(GatewayBase[BandACPServerAdapter])`,
`SlackGateway(GatewayBase[SlackAdapter])`,
`A2AGateway(GatewayBase[A2AGatewayAdapter])`). Use ``gateway.adapter`` for a
typed, validated handle — wrong adapter types raise ``TypeError`` naming the
gateway class. Lifecycle hooks (``_start_resources`` / ``_stop_resources`` /
``_serve_transport``) are abstract and fail at construction if missing.

```python
from band import A2AGateway, ACPGateway, SlackGateway
from band.adapters import A2AGatewayAdapter
from band.integrations.acp import BandACPServerAdapter
from band.integrations.slack import SlackAdapter

class NotTheRightAdapter:
    async def on_started(self, agent_name, agent_description) -> None: ...

agent = Agent.create(adapter=NotTheRightAdapter())

for gateway_cls, adapter_cls in [
    (A2AGateway, A2AGatewayAdapter),
    (ACPGateway, BandACPServerAdapter),
    (SlackGateway, SlackAdapter),
]:
    # A gateway is not an alias for the adapter it owns.
    assert gateway_cls is not adapter_cls
    try:
        gateway_cls(agent=agent)
    except TypeError as error:
        assert str(error) == (
            f"{gateway_cls.__name__} requires {adapter_cls.__name__}, "
            "got NotTheRightAdapter"
        )
    else:
        raise AssertionError(f"{gateway_cls.__name__} accepted the wrong adapter")
```

## Phase 4 — A2A Gateway host

Foreground gateway ownership replaces the adapter-hosted HTTP server started by
``Agent.run()``.

| Old spelling | New spelling |
|---|---|
| ``await agent.run()`` with ``A2AGatewayAdapter`` | ``async with A2AGateway(agent=agent) as gateway: await gateway.serve()`` |
| Adapter starts HTTP in ``on_started`` (background task) | ``A2AGateway`` owns foreground ``serve()`` |

```python fixture:a2a_agent
from band import A2AGateway
from band.adapters import A2AGatewayAdapter

# ``a2a_agent`` is a constructed-but-not-started Agent (fixture mocks the
# runtime + HTTP serve so this fence stays offline).
async with A2AGateway(agent=a2a_agent) as gateway:
    assert gateway.state == "started"
    await gateway.serve()
    assert isinstance(gateway.adapter, A2AGatewayAdapter)
    assert gateway.state == "started"

assert gateway.state == "stopped"
assert A2AGateway is not A2AGatewayAdapter
```

**Rules**

- Pass a constructed-but-not-started ``Agent``; an already-running agent or the
  same agent bound to two gateways raises ``LifecycleError``.
- ``A2AGatewayAdapter(manage_http_server=True)`` (default) still works for one
  release but emits ``BandDeprecationWarning``; prefer ``A2AGateway``.
- ``A2AGateway`` is **not** an alias for ``A2AGatewayAdapter``.
## Phase 4 — ACP Gateway host

Foreground gateway ownership replaces manually starting the agent and calling
``run_agent(server)`` yourself.

| Old spelling | New spelling |
|---|---|
| ``await agent.start(); await run_agent(server)`` | ``async with ACPGateway(agent=agent, server=server) as gateway: await gateway.serve()`` |
| ``async with agent: await run_agent(server)`` | ``async with ACPGateway(agent=agent, server=server) as gateway: await gateway.serve()`` |

```python fixture:acp_agent fixture:acp_server
from band import ACPGateway
from band.integrations.acp import BandACPServerAdapter, ACPServer

# ``acp_agent`` / ``acp_server`` are fixture-built (runtime + ``run_agent`` mocked).
async with ACPGateway(agent=acp_agent, server=acp_server) as gateway:
    assert gateway.state == "started"
    await gateway.serve()
    assert isinstance(gateway.adapter, BandACPServerAdapter)
    assert isinstance(acp_server, ACPServer)
    assert gateway.state == "started"

assert gateway.state == "stopped"
assert ACPGateway is not BandACPServerAdapter
assert ACPGateway is not ACPServer
```

**Rules**

- Pass a constructed-but-not-started ``Agent``; an already-running agent or the
  same agent bound to two gateways raises ``LifecycleError``.
- Wire push handlers, routers, and other adapter configuration **before**
  constructing ``ACPGateway``; the gateway only owns lifecycle and transport.
- ``ACPGateway`` is **not** an alias for ``BandACPServerAdapter`` or ``ACPServer``.
## Phase 4 — Slack Gateway host

Foreground gateway ownership replaces mount-router-yourself +
``Agent.run()`` / ``agent.run_forever()`` for Slack ingress.

| Old spelling | New spelling |
|---|---|
| ``await agent.run()`` / ``agent.run_forever()`` with ``SlackAdapter`` (socket) | ``async with SlackGateway(agent=agent) as gateway: await gateway.serve()`` |
| Mount ``slack.router`` + run uvicorn alongside ``agent.run_forever()`` (http) | ``async with SlackGateway(agent=agent) as gateway: await gateway.serve()`` |

```python fixture:slack_agent
from band import SlackGateway
from band.integrations.slack import SlackAdapter

# ``slack_agent`` is fixture-built (runtime + Socket Mode serve mocked).
async with SlackGateway(agent=slack_agent) as gateway:
    assert gateway.state == "started"
    await gateway.serve()
    assert isinstance(gateway.adapter, SlackAdapter)
    assert gateway.state == "started"

assert gateway.state == "stopped"
assert SlackGateway is not SlackAdapter
```

**Rules**

- Pass a constructed-but-not-started ``Agent`` with a ``SlackAdapter``; an
  already-running agent or the same agent bound to two gateways raises
  ``LifecycleError``.
- ``SlackAdapter(manage_ingress=True)`` (default) still starts Socket Mode
  listeners in ``on_started`` for one release but emits ``BandDeprecationWarning``;
  prefer ``SlackGateway``.
- ``SlackGateway`` is **not** an alias for ``SlackAdapter``.

