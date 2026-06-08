# Tier-1 conformance harness: model-output injection & dispatch/emission observation

**Status:** ratified. This document defines the contract the Tier-1 isolated
conformance tests build against, plus the ratified family taxonomy (INT-826) and
per-adapter Tier-1 verdicts (INT-827, Section 5). Two runnable, no-secrets
reference proofs back it: `tests/framework_conformance/test_injection_proof_spike.py`
(LangGraph + Anthropic) and `tests/framework_conformance/test_codex_injection_spike.py`
(Codex, replayed against a real `codex app-server` wire capture). These are
*spikes* — they validate the contract is buildable and honest; they are not the
production harness.

---

## 1. What this contract covers (and what it does not)

Tier-1 proves an adapter's behaviour **in isolation** — no live model, no live
platform, no secrets, on every PR. A Tier-1 test supplies a fixed *model decision*
and asserts a deterministic, observable result.

The observable results split into three seams. **This contract owns two of them:**

| Seam | Owned here? | What it proves |
|---|---|---|
| **Tool-call dispatch** | ✅ yes | given a supplied decision to call tool `T(args)`, the dispatch reaches `T` with `args` — routing, not effect |
| **Execution-event emission** | ✅ yes | as a turn runs, `tool_call` / `tool_result` (and thought/task) events are emitted, ordered, correlated |
| **Request-construction reads** | ❌ no | the prompt/history/roster/capability-gating present in the request the model is given |

The third seam — *what was assembled into the request the model sees* — rides on a
different mechanism (a "read the built request" access point + the **Tier-1
platform stand-in**), and is **out of scope here**. So is everything Tier-2 (live
model + platform), which belongs to the **agent-under-test runner** and the
**Tier-2 environment / Human API driver**.

Concretely, per conformance level, the rows this contract is responsible for:

| Level | Rows owned by this contract |
|---|---|
| L0 Platform adaptation | chat-tool dispatch (1 of 4 Tier-1 tests) |
| L1 Custom prompt & tools | custom-tool dispatch (1 of 3) |
| L2 Context fidelity | none (pure request-construction) |
| L3 Multi-participant | none (no new tool; inherits L0 dispatch) |
| L4 Rehydration | none (rebuilt-request state reads) |
| L5 Capabilities | memory/contacts dispatch (1 of 5) |
| L6 Observability | all emission rows |

---

## 2. The problem this contract solves

A canned "the model decided to call tool `T`" is **not portable**. It diverges on
two axes at once:

- **Provider format** — OpenAI, Anthropic, and Gemini encode a tool call
  differently.
- **Framework consumption** — LangGraph reads a tool call as a typed field on the
  model message; another framework runs a separate structured-generation pass and
  never consumes a provider tool-call at all.

Left to each implementer, that is N bespoke mocks — and a scorecard built on N
bespoke mocks is not comparing like with like. The harness is shared
infrastructure and must be designed once.

---

## 3. Injection: one neutral script, a per-family seam

### 3.1 The neutral model-decision representation

A Tier-1 input is a **script**, not a single decision: adapters run a tool loop,
invoking the model repeatedly (call tool → see result → decide again). One
decision is consumed per model invocation.

```python
@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str | None = None

@dataclass(frozen=True)
class ModelDecision:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

ModelScript = list[ModelDecision]   # e.g. [call thenvoi_send_message(...), then stop]
```

The test author expresses the decision **once**, in this neutral form. A thin
per-framework translator renders it into that framework's native model output.

### 3.2 The injection point is declared per adapter — not one global layer

There is no universal injection layer. The right model is: the harness fixes the
**neutral decision shape** and the **observation convention**, and **each adapter
family declares the seam it consumes a decision at**. The translator installs the
faked decision at that seam; everything downstream (tool binding, tool-call
parsing, the dispatch loop) runs **for real** on top of it.

| Seam type | Adapter(s) | Where the faked decision is installed |
|---|---|---|
| `INJECTABLE_MODEL_OBJECT` | LangGraph, PydanticAI, Google ADK | a fake/scripted model object installed at a declared seam (the `llm=` ctor arg, `Agent.override(model=...)`, or instance-substitution of `_create_runner`) |
| `INTERNAL_CLIENT_CALL` | Anthropic, Gemini | substitute the adapter's declared model-call seam (the `_call_*` method) |
| `SCRIPTED_PROTOCOL_CLIENT` | Codex | a scripted protocol-event client installed via the existing `client_factory` |

> The full taxonomy — all four families, per-adapter status, and the N-A
> conclusions — is ratified in **Section 5**. This table is the orientation
> subset; Section 5 governs.

**Declared-seam rule:** the seam each adapter exposes for substitution is a
*declared* property of that adapter, pinned by a conformance test that fails if the
seam is renamed or removed. No injection hooks leak into production constructor
signatures — instance-substitution of a declared internal method (e.g.
`_call_anthropic`, `_create_runner`) is the blessed pattern, **not** a test-only
constructor argument.

---

## 4. Observation: a dual path

The thing under test is *routing*, observed at the point the framework hands a tool
call off for execution. There are **two distinct dispatch paths**, and the contract
must observe both:

### 4.1 Platform tools → the shared recorder

Every adapter receives the platform tools as a single `AgentToolsProtocol` argument
and dispatches through it (`execute_tool_call`, `send_message`, …). So the harness
passes **one shared recording implementation** and reads dispatch off it. The
adapter's *real* tool-exposure path runs (schema generation, native-tool
conversion, MCP server — all of it); only the leaf implementation is the recorder.

This makes the **make-or-break rule** automatic: stubs are registered **through the
adapter's real exposure path**, never directly with the framework — because the
protocol argument is the only way tools enter the adapter.

### 4.2 Custom tools → the registered handler stub (different path!)

Custom (developer-supplied) tools **do not** go through the platform recorder. They
dispatch via a separate path — a custom-handler executor, or a native framework
tool whose own function runs. So custom-tool dispatch is observed by making the
**registered custom handler itself the signature-logging stub** and reading its log.

> A naïve "watch the recorder" convention would silently miss every custom-tool
> test. The observation convention **must** specify both: platform tools on the
> recorder, custom tools on the registered handler.

### 4.3 Emission

Execution events (`tool_call`, `tool_result`, and where the framework supports them
`thought` / `task`) are emitted through the same `AgentToolsProtocol` event method,
so the shared recorder captures them too. With execution emission enabled, the
assertions are: canonical type strings, invocation order preserved across multiple
tool calls, each result correlated to its originating call, non-empty payloads.

---

## 5. The family taxonomy (ratified — INT-826 / INT-827)

The original feasibility pass lumped every non-injectable adapter into one
undifferentiated "N-A" bucket organised by runtime topology (subprocess vs remote
vs framework-internal). That axis is wrong. The honest dividing line is **who owns
the code where the model's decision becomes a tool call** — and it cuts across
topology. The mechanical test is a one-line grep: which adapters call
`tools.execute_tool_call(...)` **in-process**? Exactly four — `anthropic.py`,
`codex.py`, `gemini.py`, `google_adk.py` — plus the two model-object adapters
whose framework dispatches for them (LangGraph, PydanticAI).

So the taxonomy axis is **seam-kind** — where the faked decision installs and
whether real dispatch is reachable in-process — and **Tier-1 status** and
**drift-risk** are separate attributes, not separate families. This keeps the
family count at the fewest that stay honest. Four families:

### 5.1 `INJECTABLE_MODEL_OBJECT`

The framework owns the loop and consumes a framework-native model object; the
faked decision is installed by swapping that object, and the framework's own
tool-binding and dispatch run on top. The public-vs-internal stability of the
scripted surface is recorded as `model_seam_kind` + `drift_risk`, **not** as a
separate family.

| Adapter | Status | `model_seam_kind` / drift | Seam |
|---|---|---|---|
| LangGraph | honest today | `PUBLIC_TEST_MODEL` / LOW | fake `BaseChatModel` via the `llm=` ctor arg (proof-backed) |
| PydanticAI | honest today | `PUBLIC_TEST_MODEL` / LOW | `with self._agent.override(model=FunctionModel(...))` around each `run_stream_events`; `self._agent` built once in `on_started`; **no ctor change** |
| Google ADK | honest, high drift | `INTERNAL_MODEL_SUBCLASS` / HIGH | **instance-substitute the existing `_create_runner`** to return an `InMemoryRunner` wrapping `ADKAgent(model=<scripted BaseLlm>)`; **no production ctor change**; version-pin `google-adk` |

ADK honesty is verified against the installed `google-adk`: a scripted `BaseLlm`
flows through ADK's real `handle_function_calls_async` → `tool.run_async` →
`_ThenvoiToolBridge.run_async` → `self._tools.execute_tool_call(...)`
(`src/thenvoi/adapters/google_adk.py:226`). The seam is `_create_runner`
(`google_adk.py:435`), substituted on the instance and pinned by the drift gate —
the same blessed pattern as `_call_anthropic`, **not** a new constructor hook
(which §3.2 forbids).

### 5.2 `INTERNAL_CLIENT_CALL`

The adapter owns the loop and calls a declared `_call_*` method that returns a
provider-native response; the faked decision substitutes that method on the
instance.

| Adapter | Status | Seam |
|---|---|---|
| Anthropic | honest today | substitute `_call_anthropic` (proof-backed) |
| Gemini | honest today | substitute `_call_gemini` (`gemini.py:359`); real `_process_function_calls` → `execute_tool_call` (`gemini.py:501`) |

### 5.3 `SCRIPTED_PROTOCOL_CLIENT`

The adapter drives an external runtime over a JSON-RPC-style protocol through an
existing `client_factory`; a server `item/tool/call` event flows through the
adapter's own loop into `execute_tool_call`. The faked decision is a scripted
protocol event tape; the adapter's parse-and-route runs for real.

| Adapter | Status | Seam |
|---|---|---|
| Codex | honest today (proof-backed) | existing `client_factory` ctor arg (`codex.py:312`); scripted `item/tool/call` → `execute_tool_call` (`codex.py:1337`). The registry pins the *consumer* of that ctor arg, `_build_client` (`codex.py:1021`), as the declared seam. |

### 5.4 `RUNTIME_OWNED_ROUTING` (N-A → Tier-2/E2E)

The decision becomes a tool call **inside a runtime loop the SDK does not own**,
or behind a trigger Tier-1 cannot drive without bypassing the real model→tool
path. This covers both in-process framework-owned routing *and* subprocess/remote
routing — the criterion is ownership of the dispatch code, not topology. Every
member is N-A by exploration, with a recorded `na_subreason` and a **required**
`tier2_coverage` pointer.

| Adapter | `na_subreason` | Why injection would fake dispatch |
|---|---|---|
| CrewAI | `IN_PROCESS_PRIVATE_PARSER` | builds `LLM(model=str)` internally (`crewai.py:223`) and the CrewAI executor owns the function-calling/tool-routing path; reaching it in isolation would require replacing the framework-owned routing surface, not just supplying a model decision |
| CrewAI-Flow | `NO_MODEL_DECISION_AT_ROUTING_BOUNDARY` | platform effects come from the Flow's terminal return via `SideEffectExecutor`; there is no single model decision to inject |
| Parlant | `IN_PROCESS_FRAMEWORK_RUNTIME` | the Parlant Application owns routing after `trigger_processing=True`; no provider tool-call to inject |
| Claude SDK | `OUT_OF_PROCESS_SUBPROCESS_DECISION` | the `claude` subprocess decides; `_process_response` is emission-only; real dispatch is an MCP callback the subprocess makes |
| OpenCode | `OUT_OF_PROCESS_SERVER_DECISION` | the opencode server calls the registered MCP backend; `_handle_event` only reports/auto-relays |
| Letta | `OUT_OF_PROCESS_REMOTE_DECISION` | the remote Letta server executes tools server-side; the adapter only observes and auto-relays final text |

**The mechanical CrewAI-vs-ADK distinction** is the load-bearing one: ADK runs its
*real* dispatch on a scripted native `FunctionCall` (honest), whereas CrewAI owns
the executor path that consumes native function calls and routes tools; replacing
that path in isolation would fake the dispatch rather than observe it. Same
"framework owns the loop" topology, opposite verdict — because the test is who
owns the decision→tool code, not where the loop runs.

### 5.5 Why no MCP "mini-tier"

An earlier draft proposed driving the in-process MCP handler directly (for Claude
SDK / OpenCode) as a separate honest signal. It was explored and **rejected**: it
bypasses the adapter's own `on_message` routing and only re-tests the shared
tool-name→method mapping already covered by `test_tool_name_drift.py` and the
tool-definition unit tests, while risking a misleading "looks proven" marker in
the scorecard. Claude SDK, OpenCode, and Letta are **honestly Tier-1 N-A → E2E** —
the model loop and the dispatch trigger live outside the SDK's process, which is
exactly what only E2E exercises. Not a default; a conclusion.

Every N-A is a deliberate, visible status in the scorecard — never a silent gap —
and carries the obligation that the adapter actually appears in the E2E matrix via
its `tier2_coverage` pointer. For the shared adapter E2E file, the drift gate also
checks that the adapter is present in `adapter_entry`'s parametrized list; a mocked
unit test or an unparametrized shared E2E pointer is not enough.

### 5.6 The `InjectionBinding` registry + fail-closed drift gate

Each adapter (except A2A / A2A-Gateway / ACP, which are protocol bridges, out of
scope) declares one binding:

```python
@dataclass(frozen=True)
class InjectionBinding:
    adapter: str
    family: Family            # INJECTABLE_MODEL_OBJECT | INTERNAL_CLIENT_CALL
                              # | SCRIPTED_PROTOCOL_CLIENT | RUNTIME_OWNED_ROUTING
    tier1_status: Tier1Status # HONEST_TODAY | HONEST_VIA_DECLARED_INTERNAL_SEAM | N_A_TIER2
    drift_risk: DriftRisk     # LOW | HIGH
    observation_paths: frozenset[ObservationPath]  # EXECUTE_TOOL_CALL | TYPED_METHODS
    # honest families:
    seam: str | None          # declared seam as "module:Class.method"; resolved by AST
    model_seam_kind: ModelSeamKind | None  # INJECTABLE_MODEL_OBJECT only
    spike_test: str | None    # repo-relative path to the runnable spike
    version_pin: str | None   # required when drift_risk == HIGH
    # RUNTIME_OWNED_ROUTING (N-A):
    na_subreason: NASubreason | None
    tier2_coverage: str | None  # repo-relative path to the compensating E2E/integration test
```

(The implemented dataclass lives in
`tests/framework_conformance/injection_registry.py`; this is the shape, field
order may differ.)

`observation_paths` is required because dispatch is not observed uniformly:
LangGraph / Anthropic / Gemini / Codex / ADK reach `execute_tool_call` (recorded
on `FakeAgentTools.tool_calls`), but **PydanticAI dispatches through typed
`AgentToolsProtocol` methods** (`ctx.deps.send_message(...)`, recorded on
`messages_sent`). A canary asserting only `tool_calls` would fail-closed on
PydanticAI despite correct behaviour — so the gate asserts dispatch across the
binding's *declared* observation set.

The gate (`test_injection_binding_drift.py`) fails closed when: an adapter has no
binding and is not excluded; a declared seam is **not defined in the adapter's
source** (checked by AST against the on-disk module, so a rename fails even in a
CI lane where the adapter's optional framework dep is absent — it never skips a
real rename); a `HIGH`-drift binding's `version_pin` does not contain the
*installed* framework version (`packaging` spec check); a `RUNTIME_OWNED_ROUTING`
binding lacks an `na_subreason`, points `tier2_coverage` outside E2E, or points at
the shared adapter E2E file without being in `adapter_entry`'s parametrized matrix;
or an N-A binding carries honest-only fields. The companion positive-routing canary
(`test_injection_canary.py`) fails closed when an honest binding has no canary
builder, or when driving the fixed canary decision through the real adapter yields
zero dispatches **carrying the exact canary args** on the binding's declared
`observation_paths` (catches "seam exists but routing went stale, or corrupts
args").

---

## 6. Findings from the proof (decisions for the implementer to carry)

1. **Dual dispatch path (Section 4.2)** is real and proven: for the same custom
   tool, the handler fired *and* the platform recorder stayed empty. Bake both
   observation paths into the harness from the start.

2. **Per-seam argument-validation asymmetry.** On the `INJECTABLE_MODEL_OBJECT`
   seam the framework validates injected args against the *real* platform tool
   schema before dispatch (so schema drift is caught for free, and malformed args
   are swallowed and never dispatch). On the `INTERNAL_CLIENT_CALL` seam the args
   are passed straight through with no pre-validation. → If the harness wants
   schema-honesty uniformly, the recorder should return the *real* platform tool
   schemas; this matters more for the `INTERNAL_CLIENT_CALL` seam. (This dovetails
   with the Tier-1 platform stand-in's schema source.)

---

## 7. Reference proofs

Two runnable, no-secrets spikes back the contract.

### 7.1 Model-output injection (LangGraph + Anthropic)

`tests/framework_conformance/test_injection_proof_spike.py`:

```bash
uv run pytest tests/framework_conformance/test_injection_proof_spike.py -v --no-cov
```

It drives **LangGraph** (`INJECTABLE_MODEL_OBJECT`) and **Anthropic**
(`INTERNAL_CLIENT_CALL`) from one neutral `ModelScript` and asserts four things:

1. **Platform tool-call dispatch** — right tool, right args, on the recorder.
2. **Custom tool-call dispatch** — fired via the handler stub, *absent* from the
   recorder (the dual path).
3. **Execution-event emission** — two tools, events ordered, paired, correlated,
   canonical types.
4. **Negative control** — a text-only decision dispatches nothing (the recorder is
   not vacuous).

Structurally the spike mirrors the production shape: a neutral `ModelScript`, a
shared recorder (the SDK's `FakeAgentTools`), and a per-adapter `InjectionBinding`
that declares the seam and supplies the translator. Adding an adapter = adding a
binding (or marking it N-A with a reason).

### 7.2 `SCRIPTED_PROTOCOL_CLIENT` against real wire output (Codex)

`tests/framework_conformance/test_codex_injection_spike.py`:

```bash
uv run pytest tests/framework_conformance/test_codex_injection_spike.py -v --no-cov
```

This spike proves the `SCRIPTED_PROTOCOL_CLIENT` seam is honest *against real
protocol output*, not a self-confirming mock. The fixture
(`fixtures/codex/codex_app_server_tool_call.jsonl`) is the **verbatim** wire
transcript of a live `codex app-server` turn that called `thenvoi_send_message`,
captured at the SDK's own wire choke point. The replay client subclasses the
**real** `BaseJsonRpcClient` and pushes the captured server frames through the
**real** `_dispatch_rpc_message` parser, so the `RpcEvent` the adapter consumes is
built by production code; the adapter's real turn loop then routes it to
`execute_tool_call`. The only faked thing is the transport bytes — and those bytes
are real Codex output. Asserts:

1. **Real-frame dispatch** — the captured `item/tool/call` routes to
   `execute_tool_call(thenvoi_send_message, {...})` with the captured args.
2. **Schema-pin** — the real parser yields the exact fields the adapter reads
   (`tool` / `arguments` / `callId`); fails loudly if a future Codex release
   reshapes them, signalling a fixture re-capture.
3. **Negative control** — remove the tool-call frame, nothing dispatches.

> The capture pattern (drive the real adapter against the real app-server, tee both
> wire directions) is the template for any `SCRIPTED_PROTOCOL_CLIENT` member: the
> fake transport replays *captured real output*, never a hand-shaped guess. The
> capture script and its provenance live alongside the run that produced the
> fixture; it runs the live app-server and spends tokens, so it never runs in CI.

### 7.3 The remaining honest-family members (Gemini, PydanticAI, Google ADK)

Each honest family now has a runnable, no-secrets spike asserting real-routing
dispatch + a negative control:

| Spike | Family | Seam exercised |
|---|---|---|
| `test_gemini_injection_spike.py` | `INTERNAL_CLIENT_CALL` | substitute `_call_gemini` on the instance; scripted `GenerateContentResponse` → real `_process_function_calls` → `execute_tool_call` |
| `test_pydantic_ai_injection_spike.py` | `INJECTABLE_MODEL_OBJECT` | `Agent.override(model=FunctionModel(...))` (public test facility, no ctor change); scripted streamed tool call → real `agent.tool` wrapper |
| `test_google_adk_injection_spike.py` | `INJECTABLE_MODEL_OBJECT` | instance-substitute `_create_runner` to wrap a scripted `BaseLlm`; real `InMemoryRunner` → `_ThenvoiToolBridge` → `execute_tool_call` |

Two findings from building these confirm the contract's design choices:

- **PydanticAI dispatches on typed methods, not `execute_tool_call`.** Its
  platform-tool wrappers call `ctx.deps.send_message(...)` directly
  (`pydantic_ai.py:168`), so the spike asserts on `messages_sent`, not
  `tool_calls`. This is the live justification for the binding's
  `observation_paths` field (§5.6): a canary watching only `tool_calls` would
  wrongly fail PydanticAI.
- **Google ADK's instance-substitution is honest.** A scripted `BaseLlm` flows
  through ADK's real `InMemoryRunner` → `_ThenvoiToolBridge.run_async` →
  `execute_tool_call` with no production-code change. The spike pins
  `drift_risk=HIGH`: the scripted `BaseLlm`/`LlmResponse` shape is ADK-internal,
  so the conformance binding carries a tested-minor `google-adk >=1.10,<1.11`
  version expectation. This is the internal seam guard, not the package extra's
  full runtime compatibility range.

---

## 8. Status of the open item (closed)

The Section 8 open item — "how to give the N-A adapters a proper conformance
signal" — is **resolved** by Section 5. The conclusion, reached by exploring each
seam rather than defaulting:

- **Newly Tier-1 honest:** Gemini (`INTERNAL_CLIENT_CALL`), PydanticAI
  (`INJECTABLE_MODEL_OBJECT`, no production change), Google ADK
  (`INJECTABLE_MODEL_OBJECT`, instance-substitution of `_create_runner`, no
  production change), Codex (`SCRIPTED_PROTOCOL_CLIENT`, proof-backed in §7.2).
- **Honestly N-A → Tier-2/E2E:** CrewAI, CrewAI-Flow, Parlant, Claude SDK,
  OpenCode, Letta — each with a recorded `na_subreason` and a required
  `tier2_coverage` pointer (§5.4). There is no honest in-isolation seam for these:
  the decision→tool code is owned by a framework loop or an external
  subprocess/server, so faking the model would require faking the dispatch (or the
  trigger). The MCP "mini-tier" was explored and rejected (§5.5).

Remaining implementation work (mechanics, not contract):

- **Build the four newly-honest spikes** — **done**: Codex (§7.2), Gemini,
  PydanticAI, Google ADK (§7.3), all runnable with no secrets.
- **Land the `InjectionBinding` registry + drift gate** (§5.6) — **done**:
  `tests/framework_conformance/injection_registry.py` holds one binding per adapter
  (every Python adapter except A2A / A2A-Gateway / ACP), and
  `test_injection_binding_drift.py` fails closed — proven against five violation
  classes: a forgotten/new adapter, a renamed honest seam, a missing
  `tier2_coverage` file, a shared E2E pointer that does not parametrize the N-A
  adapter, and a HIGH-drift binding without a `version_pin`.
- **Positive-routing canary** — **done**:
  `tests/framework_conformance/test_injection_canary.py` drives one fixed canary
  decision (`thenvoi_send_message(content="CANARY", ...)`) through every honest
  binding's declared seam via the real adapter and asserts ≥1 dispatch on that
  binding's declared `observation_paths`. It is fail-closed and registry-driven: a
  newly-honest binding with no canary builder fails the gate, and a seam that still
  *resolves* but no longer *routes to the recorder* fails too. Proven against three
  violation classes — a newly-honest adapter with no builder, a wrong-args route,
  and a binding pointed at the wrong observation bucket (the live proof that
  `observation_paths` is load-bearing: PydanticAI dispatches via typed methods, so
  asserting `execute_tool_call` for it correctly fails).
- **Recorder schema source** — wire the recorder's tool schemas to the real
  platform schema source (Finding 2), shared with the Tier-1 platform stand-in.
- **Per-tool / per-level coverage** — extend each honest spike from one tool per
  dimension to each chat tool (L0), custom tools (L1), and capability tools (L5).

The request-construction reads (prompt, history, roster, capability-gating,
rebuilt-request) are **not** this contract — they belong to the Tier-1 platform
stand-in.
