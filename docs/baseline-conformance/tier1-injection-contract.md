# Tier-1 conformance harness: model-output injection & dispatch/emission observation

**Status:** design baseline, proof-backed. This document defines the contract the
Tier-1 isolated conformance tests build against. A runnable reference proof lives
at `tests/framework_conformance/test_injection_proof_spike.py` (8/8 green for
LangGraph + Anthropic). It is a *spike* — it validates the contract is buildable
and honest; it is not the production harness.

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
| `INJECTABLE_OBJECT` | LangGraph | a fake model object passed via the `llm=` constructor arg |
| `INTERNAL_CLIENT` | Anthropic, Gemini | substitute the adapter's declared model-call seam (e.g. the `_call_*` method / provider client) |

**Declared-seam rule:** the seam each adapter exposes for substitution is a
*declared* property of that adapter, pinned by a conformance test that fails if the
seam is renamed or removed. No injection hooks leak into production constructor
signatures.

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

## 5. Per-adapter feasibility

One criterion decides whether an adapter is testable in isolation:

> **Does the SDK own the seam where the model's decision becomes a tool call?**

- **Yes → Tier-1 injectable.** The SDK exposes a model-output seam; fake the
  decision, real routing runs on top.
- **No → N-A in isolation → covered at Tier-2 only**, with a recorded reason. The
  decision is made inside a runtime the SDK does not own.

| Adapter | Isolation status | Reason |
|---|---|---|
| LangGraph | injectable (`INJECTABLE_OBJECT`) | model is a swappable `llm=` object |
| Anthropic | injectable (`INTERNAL_CLIENT`) | adapter owns the loop; model call is a declared seam |
| Gemini | injectable (`INTERNAL_CLIENT`) | same shape as Anthropic |
| Parlant | N-A → Tier-2 | separate structured-generation engine; no provider tool-call to inject |
| CrewAI, CrewAI-Flow | N-A → Tier-2 | framework owns the model+routing loop; faking the model would require faking dispatch |
| Google ADK | N-A → Tier-2 | framework-internal runner owns the loop |
| Claude SDK, Codex | N-A → Tier-2 | model is an external subprocess |
| Letta, OpenCode | N-A → Tier-2 | model is a remote server |

Every N-A is a deliberate, visible status in the scorecard — never a silent gap —
and carries the obligation that the adapter actually appears in the Tier-2 matrix.
How to give these N-A adapters a proper conformance signal beyond Tier-2 is still
an open question (see Section 8).

---

## 6. Findings from the proof (decisions for the implementer to carry)

1. **Dual dispatch path (Section 4.2)** is real and proven: for the same custom
   tool, the handler fired *and* the platform recorder stayed empty. Bake both
   observation paths into the harness from the start.

2. **Per-seam argument-validation asymmetry.** On the `INJECTABLE_OBJECT` seam the
   framework validates injected args against the *real* platform tool schema before
   dispatch (so schema drift is caught for free, and malformed args are swallowed
   and never dispatch). On the `INTERNAL_CLIENT` seam the args are passed straight
   through with no pre-validation. → If the harness wants schema-honesty uniformly,
   the recorder should return the *real* platform tool schemas; this matters more
   for the `INTERNAL_CLIENT` seam. (This dovetails with the Tier-1 platform
   stand-in's schema source.)

---

## 7. Reference proof

`tests/framework_conformance/test_injection_proof_spike.py` — runnable, no secrets:

```bash
uv run pytest tests/framework_conformance/test_injection_proof_spike.py -v --no-cov
```

It drives **LangGraph** and **Anthropic** (the two injectable seam types) from one
neutral `ModelScript` and asserts four things:

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

---

## 8. What the implementer decides next

This contract fixes the *shape*; the implementation chooses the mechanics. Open
items:

- **Gemini binding** — same `INTERNAL_CLIENT` pattern as Anthropic; build and
  confirm against the real adapter.
- **Recorder schema source** — wire the recorder's tool schemas to the real
  platform schema source (Finding 2), shared with the Tier-1 platform stand-in.
- **Binding registry + drift gate** — register an `InjectionBinding` per adapter
  (injectable, or N-A with reason), gated by the existing config-drift mechanism so
  a new adapter cannot be added without declaring its status.
- **Per-tool / per-level coverage** — the spike proves the mechanism on one tool
  per dimension; extend to each chat tool (L0), custom tools (L1), and capability
  tools (L5).
- **Conformance coverage for N-A adapters (open).** The adapters that are N-A in
  isolation (Section 5) currently have no conformance signal except Tier-2/E2E. We
  still need to explore a proper way to test these integrations in the conformance
  suite — e.g. whether a lighter or differently-shaped isolation harness fits an
  externally-run model loop (subprocess / remote / framework-internal), or whether
  the contract should define a distinct conformance path for them. Until that is
  resolved, their Tier-1 status is N-A by design, not by solution.

The request-construction reads (prompt, history, roster, capability-gating,
rebuilt-request) are **not** this contract — they belong to the Tier-1 platform
stand-in.
