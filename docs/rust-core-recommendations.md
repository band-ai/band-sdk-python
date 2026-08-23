# Band SDK: A Shared Rust Core for Every Language SDK

*Where the line sits between the shared Rust core and each language's SDK —
Python and TypeScript today, and any that follow.*
*Written against `band-sdk-core` @ v0.2.0, 20 August 2026.*

Authored by Gavrie Philipson, based on input from and discussions with Amit
Gazal, Nir Singher and Alexander Zaikman.

---

## Contents

| Section | |
|---|---|
| [Terms](#terms) | The vocabulary, for readers without a Rust background |
| [Recommendation](#recommendation) | The answer, in half a page |
| [1. What the code says](#1-what-the-code-says) | What each layer weighs, and what the bindings export today |
| [2. Why this line, specifically](#2-why-this-line-specifically) | The core argument — one runtime or two |
| [3. The line is currently drawn too low](#3-the-line-is-currently-drawn-too-low) | ~520 lines of policy stranded on the I/O side |
| [4. What the SDKs already re-implement](#4-what-the-sdks-already-re-implement) | ~900 duplicated lines, and the drift already shipping |
| [5. Three transports is the right answer](#5-three-transports-is-the-right-answer) | Three thin drivers over one brain, kept honest by a shared corpus |
| [6. REST should not cross the boundary at all](#6-rest-should-not-cross-the-boundary-at-all) | The largest reclaimable investment in the repo |
| [7. Binding strategy](#7-binding-strategy) | PyO3 for Python, wasm for TypeScript — with the boundary cost measured |
| [8. Packaging, release, and versioning](#8-packaging-release-and-versioning) | The wheel matrix, and how a core fix reaches a user |
| [9. Maintenance and ownership](#9-maintenance-and-ownership) | Who fixes a Rust bug, and who is on call |
| [10. Migration, keeping both SDKs shipping](#10-migration-keeping-both-sdks-shipping) | Six phases, each shipping independently |
| [11. What we should avoid: a summary](#11-what-we-should-avoid-a-summary) | The recap — every decision above, as its failure mode |
| [12. Issues to address in `band-sdk-core`](#12-issues-to-address-in-band-sdk-core) | Four concrete defects, independent of where the line lands |
| [Settled](#settled) | Decisions already taken, and what would reopen them |
| [Open questions](#open-questions) | What still needs an answer from the team |
| [Appendix A: What a Go SDK would change](#appendix-a-what-a-go-sdk-would-change) | Nothing — and why that independently tests §2 |

---

## Terms

*This document is meant to be readable without knowing Rust, Python or
TypeScript. This section defines the vocabulary the argument uses. Skip to
[Recommendation](#recommendation) if it is already familiar.*

### The situation, in plain terms

A Band agent talks to the platform two ways: over a long-lived **WebSocket**
connection carrying live events — messages, participants joining, rooms
appearing — and over ordinary **REST** calls for everything else. The REST half
is easy. The live half is not. Connections drop, the backend restarts, a newer
session supersedes an older one, and after every interruption the agent has to
work out which rooms to re-subscribe to, what it missed while it was away, and
whether to try again at all or give up. That recovery logic is the hardest and
most consequential code in an SDK, and today it exists three times over: written
by hand in Python, again in TypeScript, and a third time in Rust.

`band-sdk-core` is the Rust project meant to end that duplication. The question
this document answers is *how much* of each SDK should become Rust — which code
is written once and shared, and which stays in each language. Share too little
and the duplication remains, along with the bugs that come from it. Share too
much and the Python and TypeScript SDKs stop behaving like Python and TypeScript
libraries: their users lose the debugging tools, the test techniques and the
logging configuration they already know, and the SDKs start to feel like thin
skins over a foreign library.

### The pieces

| Name | What it is |
|---|---|
| **the platform** | the Band backend that agents connect to |
| **`band-sdk-python`, `band-sdk-typescript`** | the SDKs users install. Each speaks the platform's protocol in its own language today |
| **`band-sdk-core`** | the new Rust repo holding the code meant to be shared. It contains three **crates** — Rust's unit of packaging, roughly what a package is in Python or a module in npm |
| **`core-protocol`** | the crate holding *decisions*: what state the session is in, whether a disconnect is worth retrying, which rooms to rejoin. It reaches nothing outside itself |
| **`core-transport`** | the crate that *acts*: opens the socket, writes bytes, makes REST calls |
| **`core-runtime`** | a third crate, 186 lines — one data structure and one constant. §3 recommends dissolving it |
| **the bindings** | the glue that lets Python and Node call into the Rust (`bindings/python`, `bindings/node`) |
| **the native Rust client** | a planned Band client written in Rust, which will use the Rust transport directly |

### Where the line sits

| Term | Meaning |
|---|---|
| **the line** | the split this document is about: what is written once in Rust and shared, versus what each SDK keeps in its own language. The failure the original proposal names is *too much in Rust* — SDKs that end up feeling like wrappers around a foreign library rather than native ones |
| **the sans-io line** | a second, narrower boundary, and §3's subject: the one *inside* the Rust core, between code that performs I/O and code that only decides. "Currently drawn too low" means ~520 lines of pure decision logic sit on the I/O side of it, where no SDK can reach them. This is a line between two Rust crates, not between Rust and the SDKs — the document keeps the two apart deliberately |
| **FFI** (foreign function interface) | the mechanism by which one language calls compiled code written in another. The "FFI boundary" is the line in concrete form — the specific set of functions Python or JavaScript is allowed to call |
| **host** | the program the shared Rust code is embedded *into*: the Python process running an agent, or the Node process. Not a server — whoever is hosting the library. "The host owns the socket" means the Python or JavaScript side opens and reads the network connection, and Rust only advises |
| **consumer** | anything that uses the shared core: the two SDKs, plus the planned native Rust client |
| **drift** | one rule implemented separately in several places and gradually coming to disagree. §4 catalogues the drift already shipping today |

### Sans-io, and why it matters

| Term | Meaning |
|---|---|
| **I/O** | touching anything outside the program: network, disk, clock, log output |
| **sans-io** | French for "without I/O": a way of writing protocol code that performs no I/O whatsoever. Rather than opening a socket itself, it is *told* what arrived and *returns a list of things it wants done* — "connect", "send a heartbeat", "close the socket" — for someone else to carry out. The payoff is that any language's networking code can drive it, and that it can be tested exhaustively with no network involved |
| **state machine** | code whose job is to remember where in a process you are and what may happen next. Here: is this session connecting, up, reconnecting, or finished? |
| **effect** | one of those returned instructions. The entire vocabulary is four of them: connect, close the socket, send a heartbeat, the state changed |
| **driver** | the loop that tells the state machine what happened and carries out the effects it returns. Under this recommendation there is one driver per language, and each is thin |
| **transport** | the code that actually moves bytes — opens the WebSocket, reads frames, makes REST calls. What a driver is mostly made of |
| **protocol vs transport** | protocol is what the messages *mean* and what to do about them; transport is *getting them across*. The recommendation shares the first and not the second |
| **pure function** | one whose result depends only on what it is handed — no clock, no network, no hidden state. Trivially testable, and safe to call from any language |

### The two runtimes

The most expensive question in this document is whether the SDKs end up running
one async runtime or two. These are the terms that argument turns on.

| Term | Meaning |
|---|---|
| **async runtime** | the scheduler that lets one program keep thousands of operations in flight without a thread for each. Both languages already have one, and they are not interchangeable |
| **asyncio** | Python's. The `async`/`await` machinery every Python Band agent already runs on |
| **Tokio** | Rust's equivalent — mature, fast, multi-threaded |
| **event loop** | the single thread at the centre of an async runtime that decides what runs next. Node has exactly one, and anything that occupies it stalls the whole program |
| **the GIL** (global interpreter lock) | CPython's rule that only one thread may execute Python at a time. A Rust thread that wants to touch anything Python must take it first, and while it holds it, the Python program is stopped. A free-threaded build without it has existed since 3.13 and is officially supported as of 3.14 — but it is a separate, opt-in interpreter, and the build everyone installs by default still has the GIL |
| **V8** | the JavaScript engine inside Node. Its values are *thread-affine* — they may only be touched from the loop thread — so anything a Rust thread produces must be queued across to it |
| **blocking** | occupying a runtime's thread with work that does not yield. Both runtimes forbid it, and neither one's tooling can see it happening on the far side of an FFI call |
| **backpressure** | what a system does when data arrives faster than it can be consumed: slow the sender, buffer it, or drop it. Handing data between two runtimes forces that choice, in a place where "drop" means losing a user's message |
| **cancellation** | stopping work already in flight. asyncio does it by raising an exception inside the running code, so cleanup blocks still run. Tokio does it by dropping the task where it stands, so there is no cleanup step at all. The same word, with materially different guarantees — §2 |
| **tracing, spans** | Rust's logging framework, and the nested "this happened inside that" structure it records. Neither reaches Python's `logging` or a Node logger unless a bridge is built for it |

### Shipping compiled code

| Term | Meaning |
|---|---|
| **PyO3, maturin** | the Rust library for writing Python extension modules, and the tool that packages them |
| **wheel** | a prebuilt Python package. A pure-Python wheel installs anywhere; a compiled one must be built for each operating system × CPU × Python version — the "wheel matrix" |
| **ABI3** | a stable CPython interface that lets one compiled wheel serve many Python versions, collapsing one axis of that matrix |
| **glibc / musl** | the two C libraries Linux distributions are built on. A compiled artifact generally needs a separate build for each |
| **NAPI** (napi-rs) | Node's interface for *native addons* — real compiled machine code loaded into the Node process. Fast and unrestricted, but one binary per platform |
| **wasm** (WebAssembly) | a portable compiled format that runs inside the JavaScript engine itself. One artifact for every platform, but sandboxed: no sockets, no threads, no clock |
| **`wasm32-unknown-unknown`** | the bare wasm build target, used here as a CI gate. Code that opens a socket or pulls in Tokio simply fails to compile for it, which turns "`core-protocol` performs no I/O" from a convention into something the build enforces |
| **zero-copy** | handing another language a pointer to memory rather than a copy of it. NAPI can; wasm copies. §7 measures how little that costs at the frame sizes Band actually sends |
| **OpenAPI, Fern** | the machine-readable specification of the REST API, and the tool that generates REST clients from it. Already a single source of truth, which is why §6 keeps REST out of the bindings |
| **DTO** (data transfer object) | a plain structure mirroring the wire format exactly, as opposed to an SDK's own idiomatic types |
| **release-please** | the automation that cuts version numbers and releases from commit messages |

### On the wire

| Term | Meaning |
|---|---|
| **WebSocket** | the long-lived, two-way connection carrying live events |
| **REST** | ordinary request/response HTTP — everything that is not live |
| **Phoenix Channels** | the messaging protocol the platform's Elixir backend speaks over that WebSocket, multiplexing many logical streams onto one connection |
| **frame** | one message on the wire |
| **topic** | the name of one logical stream, e.g. `chat_room:<id>` |
| **`phx_join` / `phx_leave`** | the frames that subscribe to and unsubscribe from a topic. Their ordering matters, and is one of the invariants §2 turns on |
| **join ack / join rejection** | the backend's answer to a join — and, when it rejects, the question of whether that kills one room or the whole session |
| **heartbeat, watchdog** | the periodic ping proving a connection is still alive, and the timer that declares it dead when no reply arrives |
| **close code** | the number a WebSocket carries when it ends. 1000 and 1001 are normal; **1008** is what the platform means as terminal — stop, do not reconnect; **1012** and **1013** mean the backend is restarting and to come back later |
| **backoff, jitter** | waiting longer after each failed reconnect, plus deliberate randomness so a fleet of agents does not all retry in the same instant |
| **catch-up** | fetching over REST what was missed while disconnected |
| **rejoin** | re-subscribing to every topic after a reconnect |
| **supersede** | the platform telling a session that a newer connection has replaced it |
| **taxonomy** | as in "the disconnect taxonomy": the closed set of named reasons a connection ended, and what each implies about whether to retry |

### A few more, used once each

| Term | Meaning |
|---|---|
| **shadow mode** | running new logic alongside the old on real production traffic, comparing the two and shipping nothing user-visible until they agree (§10) |
| **Hermes** | React Native's JavaScript engine, which has no wasm support — the one runtime gap in §7. Unrelated to the agent framework of the same name |
| **Sev-1** | a top-severity production incident, where how fast a fix can ship is what matters |
| **YAGNI** | "you aren't gonna need it" — do not build for a requirement that is not real yet |

---

## Recommendation

**Ship `core-protocol` across the FFI boundary. Let every consumer own its own
I/O.**

The shared core should be the sans-io protocol logic — the session state
machine, the reconnect planner, the disconnect taxonomy, the Phoenix frame
codec, the rejoin policy. Each consumer — the Python SDK, the TypeScript SDK,
and the planned native Rust client — drives that logic with its own transport,
on its own runtime, using its own sockets.

That means `core-transport` stays, and is genuinely valuable, but as **the
native Rust client's transport** (e.g. for use in Jam or other Rust-based tools) rather than as the SDKs' delivery vehicle. And it
means the REST client should not cross the boundary at all.

This is the boundary already drawn in the crate graph. The recommendation is
mostly to *honour* it — the bindings currently cross it in the wrong place, and
about 520 lines of pure logic sit on the wrong side of it (§3).

The end state is **two crates**: `core-protocol`, everything sans-io, which is
the binding surface; and `core-transport`, the I/O drivers, for the native Rust
client. `core-runtime` folds into the first.

---

## 1. What the code says

### The layers, by weight and character

| Layer | Rust (src) | Character | What the SDKs use today |
|---|---|---|---|
| `core-protocol` | 2,153 (+2,769 test) | stateful, subtle, **zero I/O, builds on wasm32** | ~1,700 lines Python, ~1,330 lines TS — hand-written, twice |
| `core-runtime` | 186 | pure — one data structure and one constant | — |
| `core-transport` — REST | 1,614 + ~1,400 DTO | stateless request/response | Fern-generated `band-client-rest`; thin TS `rest/` |
| `core-transport` — socket | 1,811 | the loop that feeds the machine — **but ~29% of it is pure policy (§3)** | `phoenix-channels-python-client`; `PhoenixChannelsTransport.ts` |

### The finding that reframes the question

Look at what the bindings actually export today:

```
UserRest, AgentRest, Connection, Tracing
```

REST accounts for **1,342 of the Python binding's 1,626 lines (83%)** and
**1,134 of Node's 1,445 (78%)**. Nothing from `core-protocol` crosses the FFI
boundary at all.

The bindings currently export the layer with the least value per line, and none
of the layer with the most.

### The effect vocabulary is four variants

The decisive technical fact. `SessionEffect` — everything the state machine can
ask a driver to do — is:

```rust
pub enum SessionEffect {
    Connect,
    CloseSocket,
    SendHeartbeat,
    StateChanged(SessionState),
}
```

`SessionInput` is roughly a dozen small tagged variants carrying strings and
booleans. Nothing else. **Message payloads never touch this machine.**

Driving it from asyncio or from a Node event loop is not an ambitious
engineering project — it is a `match` over four cases plus a timer. This is the
whole argument for the line in one type definition: the interface is thin,
coarse, and infrequent, which is exactly what an FFI boundary should be.

---

## 2. Why this line, specifically

**The drift you are paying for is protocol drift, not socket drift.** Nobody
re-derives "open a TCP connection" wrongly twice. What does get re-derived
wrongly is: when is a session `Up`? (ack-gated on every join *and* catch-up). Is
this disconnect retryable? What is the backoff schedule? Does `phx_leave` go out
strictly before the new `phx_join` on eviction? Does a deferred `room_added`
survive a reconnect? That is `core-protocol`, and it is the part carrying 2,769
lines of tests.

**Sans-io exists for exactly this.** `apply(input, now) -> [effects]` is a pure
function. No runtime to own, no threads to bridge, no cancellation model to
reconcile. It crosses FFI synchronously on the host's own thread. The pattern's
whole promise is that a foreign runtime can drive it — collecting that dividend
is the point of having written it this way.

**Owning the socket in the host runtime is what keeps the SDKs feeling native.**
Three questions decide whether an SDK reads as native or as a wrapper around a
foreign library:

| | Rust owns transport | Host owns transport |
|---|---|---|
| Can an operator diagnose a stuck reconnect in the host's tooling? | No | Yes |
| Can a test inject a fake transport? | No | Yes |
| Do transport logs obey the host's logging config? | No | Yes |

The third is not hypothetical: Rust's `tracing` does not reach Python's
`logging`, so a Rust-owned transport would put the SDK's most important
subsystem outside `BAND_LOG_*` — precisely where operators go when debugging a
reconnect storm. §12 gives the mechanism, and the two disjoint logging systems
a Python operator is left with today.

The second is not hypothetical either. `band-sdk-python` carries roughly 123k
lines of tests, a good part of which fake the transport. A Rust-owned socket is
opaque to all of it.

**Backpressure disappears.** If Rust owns the socket, every inbound message
originates on a Rust thread and must be scheduled onto the host loop — bounded
queue, drop policy, backpressure semantics, and an `asyncio.CancelledError`
that reaches the Tokio task — if at all — as a silent drop. If the host owns the
socket, message data
never leaves the host runtime and none of those questions arise. The Node
tracing bridge is a small preview of that problem; the data path would be the
full version of it.

### Two I/O runtimes in one process

The three rows above are symptoms, and the first of them — whether an operator
can make sense of a stuck reconnect — is the deepest. What sits underneath all
three is the decision itself: **"Rust owns transport" means a second I/O runtime
lives inside the host's process for the life of the program** — Tokio's
multi-threaded scheduler alongside asyncio's loop, or alongside Node's. Real
software does this and it can be made to work. But the costs multiply rather
than add, none of them show up in a hello-world, and all of them become
permanent the moment an SDK's users depend on the surface.

**The seam already exists in this repo, so it can be described exactly rather
than in the abstract.** `bindings/python/src/lib.rs` builds a multi-thread Tokio
runtime at import time (threads named `band-sdk-core-tokio`), and every method
returns `pyo3_async_runtimes::tokio::future_into_py`. One `await` on one of them
runs: the coroutine suspends on an `asyncio.Future`; the Rust future is polled
on a Tokio worker; on completion the bridge hops to `spawn_blocking` *purely to
take the GIL* — its own comment says holding the GIL inside the runtime "may
prevent other tasks from progressing" — then `call_soon_threadsafe` wakes the
loop, which resolves the future on its next turn. Rust awaiting a Python
coroutine is the mirror image: `call_soon_threadsafe` → `ensure_future` →
`add_done_callback` → a `oneshot` back. That is what a *correct* bridge looks
like, and the reason for walking through it is that **no step in it is
removable**. Each one is there because one runtime enforces a rule the other
knows nothing about: a Rust future has to be polled by a Rust executor; the GIL
cannot be taken on a runtime worker without risking the runtime; only the loop
thread may touch loop state; and the loop yields control on its own schedule.
Five steps is therefore the *floor* on what a single `await` costs — not a first
draft that a later contributor will tighten up. The complexity is not a defect
in this binding that better engineering would remove. It is the price of the
seam, and it is charged on every crossing.

**One lock, two schedulers, no arbiter.** Every Rust→Python crossing must take
the GIL, and every Python callback holds it. A busy interpreter therefore stalls
Tokio workers, and a Rust worker taking the GIL delays the loop — and the work
now contending is asymmetric in importance. Heartbeats, socket reads and
reconnect timers are health-critical and latency-sensitive; application work is
neither. Neither scheduler can see the other's queue, so neither can prioritise,
and a validation storm in one room can delay the heartbeat that keeps the whole
session alive. Free-threaded CPython does not remove this so much as trade it —
lock contention becomes an ordering and soundness question instead — and it
remains a separate opt-in build rather than the interpreter an SDK's users are
running. Node's version is stricter rather than looser: V8 values are
thread-affine, so nothing produced on a Rust thread may touch JS without first
being queued onto the one loop thread. That is exactly why the existing tracing
bridge is a `ThreadsafeFunction` with a 1024-slot queue and drop-on-full. Put message data
on that path and drop-on-full stops being a logging policy and becomes a
data-loss policy.

**Two cancellation models that do not mean the same thing.** asyncio cancels by
*throwing*: `CancelledError` is delivered into the coroutine at its next
suspension point, `except`/`finally` run, cleanup may itself await, and the
traceback survives. Tokio cancels by *dropping*: the future stops at its last
`.await`, nothing is raised, and `Drop` cannot await. The bridge does connect
them — cancelling the `asyncio.Future` drops the Rust future — but wiring two
models together does not make them equivalent, and the asymmetry lands squarely
on this protocol's invariants. This section opened by asking whether
`phx_leave` goes out strictly before the new `phx_join` on eviction. In Python
that invariant is a `try/finally`. In a dropped Rust future it is
unreachable: there is no await point left from which to send the frame. The
guarantee a reader sees in the `finally` is enforceable on one side of the
boundary and structurally unenforceable on the other.

There is a race underneath it, too. When a completion and a cancel cross
mid-flight, the bridge checks `future.cancelled()` and, if set, discards the
result — it must, since an `asyncio.Future` cannot be resolved twice. Correct,
and it means a reconnect that actually succeeded can be reported to the caller
as cancelled, with only the Rust side any the wiser.

**Two shutdowns, one process exit.** They have to be wound down in an order
neither of them knows about. Drop the Tokio runtime from the loop thread and you
can deadlock outright: the drop blocks until blocking tasks finish, and the
bridge's GIL hop *is* a blocking task — waiting for the GIL held by the very
thread performing the drop. Wind the loop down first instead, and late
completions call into a closed loop — `RuntimeError: Event loop is closed`,
raised on a thread with no Python traceback to attach it to. Node's mirror is
process lifetime: a pending handle keeps the process alive (the binding's own
doc notes that a pending `next()` refs it, and the tracing callback is
deliberately `Weak = true`), so
the failure is a process that will not exit, or one that exits while Rust
threads are mid-write. Each of these is fixable. Each fix is a rule that lives
in no type system, is rediscovered during an incident, and is re-broken by the
next contributor.

**Blocking becomes invisible.** Both runtimes have the same rule — never block
the executor — and the rule is only enforceable where you can see. A synchronous
FFI call that blocks in Rust freezes the entire event loop, and asyncio's
debug-mode slow-callback warning names the Python callback, not the Rust work
inside it. In
the other direction, a user's synchronous handler — a blocking `requests` call
in a message callback — stalls Tokio workers while holding the GIL.
`PYTHONASYNCIODEBUG` sees Python. `tokio-console` sees Tokio. The seam between
them has no observer at all.

**And these fail in production rather than in tests.** Every failure above is
load- and timing-dependent: a queue that never fills with one room fills with
fifty, GIL contention needs a busy interpreter, the shutdown deadlock needs a
socket mid-write. Ordinary unit tests do not produce those conditions — and the
technique that would, a fake transport, is the very thing a Rust-owned socket
takes away.

### Why the debugging cost is the one that compounds

"Can a user set a breakpoint" is the wrong question; very little async debugging
happens that way. The real one is: **when a reconnect wedges in production, can
an operator reconstruct what happened from the record the process left behind?**
For async systems the answer is yes, given tracing and spans — within *one*
runtime. Three properties make that work, and each is a property of the single
runtime rather than of tracing itself:

| What makes an async system diagnosable | One runtime | Two runtimes |
|---|---|---|
| A causal ordering of events | the scheduler establishes it | two clocks, merged on timestamps |
| Context that propagates itself | span / contextvar, automatic | stops at the boundary |
| One "what is running right now" | `asyncio.all_tasks()`, `tokio-console` | half an answer from each |

- **Context does not cross.** A `tracing` span is a Tokio task-local; a
  contextvar is an asyncio task-local. Neither is visible to the other as a
  value. (The bridge does carry `TaskLocals`, so contextvars survive a
  Rust→Python call — but that returns Python's context to Python; it never gives
  a Rust span a Python parent, or the reverse.) One logical operation therefore
  produces two disconnected trace trees, and joining them means threading a
  correlation id by hand through every signature that crosses, in both
  directions, permanently. §12 records the small version of this already in the
  repo: no spans exist in the core at all, and `correlation_id` survives only as
  a flat event field.
- **Ordering degrades to wall clock.** Within one runtime, "A then B" is a fact
  its scheduler establishes. Across two it is two log streams merged on
  timestamps, one of them buffered through a bounded queue that drops on
  overflow — and overflow happens under exactly the load you are trying to
  reconstruct. The record thins out precisely where the incident is.
- **Stacks stop at the boundary.** A Python traceback ends at the extension
  frame; a Rust backtrace contains no Python frames. A panic on a Tokio worker
  is not an exception in the calling coroutine at all — it surfaces through
  `catch_unwind` at the export, far from where it happened. The binding carries a
  `debug_panic` endpoint specifically to pin that behaviour down, which is a fair
  measure of how much explaining it needs.
- **The bugs are interleaving bugs, so you cannot re-run them.** A single
  runtime's failures live in the interleavings its scheduler can produce, and its
  tooling is built to model that space. A second scheduler does not add a second
  space — it multiplies, because any state of one can pair with any state of the
  other, and nothing can pause both at once to look. The tools cover the factors;
  the bugs live in the product.

That last point is the argument in miniature. Tracing works because a span tree
encodes causality *inside* one runtime. Two runtimes give you two span trees and
a gap where the causal edge should be, and the gap is where cross-runtime bugs
concentrate: a cancelled awaiter over a task still running, a completion
discarded by a cancel that raced it, a loop turn that never came. None of those
have a home in either trace. So "async is debuggable if you instrument it with
tracing and spans" is true, and does not rescue this case: the seam is the one
region neither ecosystem's tooling models, and it is the region this decision
creates.

### What sans-io removes

Set every cost above against `apply(input, now) -> [SessionEffect]`. It is a
synchronous pure function with no runtime behind it. The host calls it on the
thread it is already running on, holding the GIL it already holds, inside the
task whose context is already current, and gets a `Vec` back before it yields.

- No second scheduler, so no contention, no priority inversion, and no
  interleaving product to search.
- No second cancellation model, because nothing is in flight to cancel — a
  caller that goes away simply stops calling.
- No second shutdown, because there is nothing running to wind down.
- No queue, therefore no bound, no drop policy, and no backpressure semantics to
  define.
- No context to propagate, because control never left the caller's task: the
  effects are logged by the host, in the host's logger, under the span that is
  already open.

The FFI stops being a runtime boundary and becomes a function call — which is
why §1's four-variant effect enum settles this. The interface is not merely
small, it is *runtime-free*.

So the choice is not "Rust's async runtime or Python's" — it is **one runtime or
two**, and everything in this section is the price of the second. Embedding a
Rust runtime is a reasonable trade when the Rust side owns I/O the host has no
equivalent for. That is not the situation here: both hosts already have a mature
async runtime and a working transport, and the duplication actually worth paying
to remove is in the protocol layer (§4), not in the socket.

---

## 3. The line is currently drawn too low

A large amount of `core-transport/socket.rs` is not transport. It is pure
decision logic — no sockets, no I/O, time already entering as a `Millis`
parameter — sitting on the I/O side of the boundary, where no SDK can reach it.

**About 520 of socket.rs's 1,811 lines (29%) can move down.** Moving them does
two things at once: it enriches what every consumer shares, and it thins the
driver each consumer has to write.

### What can move, item by item

| Item | Lines | ~LOC | State today |
|---|---|---|---|
| `HumanSocketParams` | 97–129 | 33 | pure — config + builders |
| `Flavor` + `base_topics` | 130–146 | 17 | pure — topic derivation |
| `HumanState` | 147–164 | 18 | pure — the human flavor's watch state |
| `RoomJoins` + `TrackedRoom` | 165–271 | 107 | pure — per-room join recovery registry |
| `WatchAction` + `watch_action` | 272–306 | 35 | pure — frame → watch-action classifier |
| `build_ws_url` | 594–612 | 19 | pure — URL construction |
| `Pending` + `fail_pending` | 771–796 | 26 | near-pure — only the oneshot sends |
| `disconnect_outcome` | 1207–1220 | 14 | near-pure — reads `Instant::elapsed` |
| `recover_from_rejection` | 1492–1561 | 70 | **pure** — join-rejection recovery policy |
| `abandon_room` | 1562–1578 | 17 | pure |
| `forget_errored_channel` | 1579–1630 | 52 | pure — channel-death policy |
| `dispatch_frame` | 1699–1811 | 113 | **near-pure** — only `events.try_send()` |

The two in bold matter most.

**`dispatch_frame` is the prize.** It is the frame router: given an inbound
frame and the current state, it decides what the frame *means* — supersede,
channel error, heartbeat reply, join ack, join rejection, or a plain event to
forward. That is precisely the logic both SDKs hand-implement today, and it is
the densest 113 lines in the file. It also carries reasoning that is expensive to
rediscover, like why a heartbeat reply is deliberately ref-agnostic so a late
reply to an older heartbeat still resets the watchdog.

**`recover_from_rejection` + `forget_errored_channel` + `abandon_room`** (139
lines) are the per-room failure policy: which rejections are fatal to the whole
session versus one room's problem, when a topic is forgotten versus retried, and
why a fatal rejection deliberately *keeps* the topic so the reconnect's rejoin
batch can retry it. All three are already pure.

### Three need a small change first

None is structural; each is a mechanical edit toward a convention
`core-protocol` already uses.

1. **`dispatch_frame`** — replace `events.try_send(view)` with a "forward this"
   variant on the `FrameOutcome` it already returns, and let the caller do the
   sending. The return-effects mechanism exists; this extends it.
2. **`disconnect_outcome`** — take `now: Millis` instead of computing uptime from
   `tokio::time::Instant`. This is exactly the `Millis`-parameter convention
   `core-protocol` documents.
3. **`fail_pending`** — split it: a pure function decides which pending refs
   failed and why, the caller performs the oneshot sends.

### The move must not bring `tracing` with it

The fourth change, and the one easiest to overlook, because it is a dependency
rather than a signature.

`core-protocol` has **zero** `tracing` call sites and does not depend on
`tracing` at all — its only dependencies are `serde` and `serde_json`. The code
proposed for the move contains **10 `tracing::` calls** (9 `warn!`, 1 `error!`).
Moved verbatim, they would put a logging dependency into the one crate that has
stayed clean, that compiles to `wasm32`, and that crosses the FFI boundary.

The fix is the discipline the crate already applies to I/O. Writing a log line
*is* I/O; a crate that returns `Vec<SessionEffect>` rather than performing
effects should **return diagnostics rather than emitting them**.

```rust
// The host is already calling in and taking a return value.
session.apply(input, now) -> Vec<SessionEffect>
// A diagnostic rides the same path — no new mechanism, no new crossing.
recover_from_rejection(..., &mut diagnostics) -> bool
```

Why this beats both alternatives — taking a `tracing` dependency, or bridging
log events across FFI:

- **No boundary crossings.** The host already receives a return value on every
  call, so a diagnostic costs nothing extra. A `tracing` bridge instead needs a
  per-event crossing from arbitrary Rust threads, with a bounded queue and a
  drop policy — the whole apparatus in `bindings/node/src/tracing_init.rs`,
  rebuilt for the protocol path.
- **The host logs natively, not approximately.** The SDK calls its own
  `logger.warning(...)` about a fact Rust returned, so `BAND_LOG_*`, handlers,
  file rotation and formatting all apply — because Python is what is doing the
  logging.
- **The wasm gate survives.** No `tracing` in the dependency tree of the crate
  that must compile to `wasm32-unknown-unknown`, and no subscriber question in a
  browser.
- **Testable without a subscriber.** Assert on returned diagnostics instead of
  captured output. `core-transport` currently carries `tracing-subscriber` as a
  dev-dependency purely so its e2e warnings print — the tell that log-as-output
  is awkward to assert on.
- **The host owns the wording.** Today `"core-transport: room event carried no
  room id; watch set unchanged"` is baked into Rust, so a Python operator gets
  English prose from a Rust string interleaved into Python logs. A typed variant
  lets each SDK phrase, level, and localise it.

**Shape.** A `Diagnostic` enum in `core-protocol`, accumulated into a
caller-provided sink. Keep it separate from `SessionEffect` rather than adding a
variant: effects are imperatives ("do this"), diagnostics are observations
("this happened"), and merging them muddies a vocabulary that is currently very
clean at four variants. Carry facts in the variant and expose a `severity()` for
a sensible default the host may override — the core knows which situations are
anomalous, but whether "gave up on a room" is a warning or an error is host
policy.

**Two of the ten resolve themselves.** Both `"events channel full, dropping…"`
warnings in `dispatch_frame` are about the tokio mpsc, not the protocol, and
they leave with the change already listed above — once `dispatch_frame` returns
what to forward instead of sending it, backpressure warnings stay with whoever
owns the channel. That leaves roughly eight genuine diagnostics.

`core-transport` keeps `tracing` exactly as it is. Rust owns that process, there
is no boundary, and it is the right idiom there.

### What genuinely stays in `core-transport`

The real I/O, and it is all of the `async fn`s: `try_connect`,
`connect_with_retry`, `run`, `run_connected`, the four `send_join`/`send_leave`
variants, `sync_rooms_to_watch`, `apply_watch_plan`, `handle_command`,
`handle_due_room_joins`, plus `publish_state`, `rest_catch_up`, `Connection`,
and `WsSubscriber`.

That is the honest shape of a driver: open the socket, write bytes, run a
`select!` loop, and hand decisions to the machine.

### Where it should go: fold, don't add

The natural question is whether this earns a fourth crate — `core-session` or
similar, for driver-level policy above the wire.

**No. The move is toward fewer crates, not more.** Two reasons, one of which is
a hard constraint.

**The hard constraint: the moved code spans two crates today.**
`RoomJoins`, `watch_action`, `recover_from_rejection`, and `dispatch_frame` all
touch `RoomWatchSet`, which lives in `core-runtime`, *and* `Session`/`Frame`,
which live in `core-protocol`. Those two are siblings — both leaves, neither
depending on the other. Pure logic needing both cannot live in either without
creating a dependency between them.

**The observation that resolves it: `core-runtime` is not carrying a crate's
worth of weight.** It is 186 lines total — `room_watch.rs` (177) plus a nine-line
`lib.rs` holding one constant, `DEFAULT_TRACING_LEVEL`. Its only dependency is
`tracing`. And per the repo's own README, both bindings depend on it *solely* to
read that constant. A crate that exists so two bindings can share one log level
is a module and a constant, not a crate.

So:

- **Fold `room_watch` into `core-protocol`.** It is the human socket's session
  policy and belongs beside `session.rs`, `rejoin.rs`, and `backoff.rs`. It
  brings no logging dependency with it: `room_watch.rs` has zero `tracing` call
  sites, and `core-runtime`'s `tracing` dependency exists *only* for the
  `DEFAULT_TRACING_LEVEL` constant — which moves to the bindings anyway. So the
  fold removes a `tracing` dependency from the sans-io side rather than adding
  one.
- **Move `DEFAULT_TRACING_LEVEL` to where it is used** — the bindings, or a
  shared constant that is not a crate.
- **Dissolve `core-runtime`.**
- **Move the ~520 lines above into `core-protocol`.**

The result is **two crates with one rule between them**: does it perform I/O? No
→ `core-protocol`. Yes → `core-transport`. That rule is mechanical, needs no
judgement at the margin, and is checkable — `core-protocol`'s existing
`wasm32-unknown-unknown` CI gate enforces it, since sockets, TLS, and Tokio
cannot compile there.

It also gives the bindings **one dependency and one wasm gate** rather than
three of each.

If the wire/policy layering is worth expressing, express it in modules —
`core_protocol::wire` and `core_protocol::session` — which costs nothing and can
be split into crates later if `core-protocol` outgrows itself. Splitting now
prices in a structure the code has not asked for.

**The alternative I considered and rejected:** make `core-runtime` the policy
crate instead, leaving `core-protocol` as pure wire types. It is coherent, but it
means moving `session.rs`, `rejoin.rs`, `backoff.rs`, and `disconnect.rs` — 1,063
lines of stable, well-tested code — across a crate boundary for no behavioural
gain, and it leaves the bindings depending on two crates instead of one. The
wire/policy distinction it buys does not exist in the current code anyway:
`core-protocol` already holds the session machine and the reconnect planner,
neither of which is a wire type.

### Do this first

Sequence matters. Do the fold **before** exposing anything through the bindings,
so the shared surface is right the first time rather than versioned into place
and then corrected. It is pure refactoring inside a private repo with zero
external consumers — the cheapest possible moment.

---

## 4. What the SDKs already re-implement

§3 looked at Rust code sitting on the wrong side of the line. This is the mirror
image: **roughly 900 lines across the two SDKs that re-implement what
`core-protocol` already holds.**

Being three hand-written copies of one wire contract, they have drifted. The
divergences below are not hypothetical risk — they are in `main` today, and
several are user-visible.

### The overlap, by area

| Area | `core-protocol` | Python SDK | TypeScript SDK |
|---|---|---|---|
| Wire payloads | `payloads.rs` (281) | `client/streaming/client.py` models (~240) | `payloadSchemas.ts` (121) |
| Event union / vocabulary | `payloads.rs` | `platform/event.py` (176), `_PAYLOAD_MODELS` | `events.ts` (48), `payloadSchemas` |
| Disconnect taxonomy | `disconnect.rs` (186) | `client/streaming/errors.py` (109) | `disconnectReason.ts` (210) |
| Topic construction | `topics.rs` (43) | 14 inline f-strings | 6 inline template literals |
| Reconnect policy | `backoff.rs` (353) | `_initial_reconnect_delay` (5 lines) + a third-party library | phoenix.js's default ladder |

### Where they have already drifted

**Payload models — required vs optional is inconsistent, and TS drops events
because of it.** `RoomRemovedPayload` has every field but `id` optional in
Python and **required** in TypeScript. TS validates with `safeParse` and, on
failure, logs `"Invalid ... payload, dropping event"` and returns. So a
`room_removed` push that omits `title` is delivered to a Python agent and
**silently dropped for a TypeScript one** — the agent never learns the room went
away. The same inversion runs the other way on `ContactRequestReceivedPayload`,
where `from_name` is optional in Rust but required in both SDKs.

**Fields that exist in one model and not another.** TypeScript's
`MessageCreatedPayload` has no `thread_id`, so threading data is dropped on the
floor. Its `ParticipantAddedPayload` carries only `id`/`name`/`type`/`handle` —
no `description`, no `is_remote`/`is_external` — which is the same gap
`band-sdk-python` shipped a fix for in `d59b0d37` ("surface participant
description in the passive roster"). The TypeScript SDK still has the bug Python
already fixed, because there was no shared model to fix once.

**The event vocabulary is three different closed sets.** Python's
`_PAYLOAD_MODELS` maps 13 event types; TypeScript's `payloadSchemas` maps 10.
TypeScript has no `message_updated` (so delivery-status transitions are invisible
to it) and no `agent.control` — it joins the `agent_control` channel but handles
only `supersede`, so interrupt/stop/play does not reach a TypeScript agent at
all. Python additionally constrains `AgentControlPayload.mode` and `.scope` to
`Literal` sets, so a *new* mode from the backend raises a `ValidationError` in
Python where Rust's open `String` accepts it.

**Disconnect taxonomy — the four codes agree, the behaviour around them does
not.** All three carry `invalid_on_conflict`/`connection_conflict`/
`too_many_requests`/`tracking_failed` with the same 400/409/429/503 statuses. But:

| Behaviour | Rust | Python | TypeScript |
|---|---|---|---|
| Unrecognized code | retained; `is_retryable()` → **false**, deliberately | no `retryable` concept at all | `z.enum` rejects the whole body → `null` |
| `retry_after` leniency | accepts whole-number floats | `int`/`str` only | `z.number()` in body, string in header |
| `Retry-After` header | — | only when status is 429 | any upgrade code |
| status ↔ code agreement | not enforced | not enforced | enforced; mismatch → `null` |

The first row is the one that bites. Add a fifth code to the backend and Rust
treats it as terminal, TypeScript falls through to a generic close reason that is
`retryable: true`, and Python has no opinion because it never models
retryability. One backend change, three behaviours.

**Reconnect policy — the operationally serious one.** `backoff.rs` implements a
close-code taxonomy (1000/1001 normal, **1008 terminal**, 1012/1013 with delay
floors), a rapid-disconnect sliding window, cooldown suppression after repeated
rapid disconnects, and a jitter band. Neither SDK handles WebSocket close codes
**at all** — grepping both trees for 1008/1012/1013 returns nothing in either.

Python retries only the *initial* connect, then sets `auto_reconnect = True` and
hands reconnection to `phoenix-channels-python-client`. TypeScript delegates to
phoenix.js's default `reconnectAfterMs` ladder. So:

- A close code **1008**, which the platform means as terminal, makes the Rust
  client stop and both SDKs **reconnect forever**.
- Codes **1012/1013** — what a backend sends during a restart — get no delay
  floor in either SDK, so a fleet of agents reconnects on the base ladder into a
  service that is still coming up.
- Neither SDK can detect a rapid-disconnect loop, because neither tracks
  connection uptime.

This is the strongest single argument in the review for sharing `core-protocol`.
It is not code hygiene: it is whether a fleet of agents stampedes a recovering
backend, and today the answer differs by language.

**Topics are magic strings.** `topics.rs` provides builders and an inverse
(`chat_room_id`). Both SDKs instead inline the format at every call site — 14
places in Python, 6 in TypeScript — with no builder and no inverse. This is the
"single source of truth for a closed vocabulary" rule in `band-sdk-python`'s own
CLAUDE.md, violated against a vocabulary the backend owns.

### What I would *not* push down

Three things look like candidates and are not. Naming them matters as much as
naming the candidates, because this is where "too much in Rust" starts.

- **Phoenix framing.** Rust hand-rolls it in `phoenix.rs` because a native client
  must. Both SDKs use mature libraries — `phoenix-channels-python-client` and
  phoenix.js — that are well-tested, idiomatic, and free. Replacing a working
  library with an FFI call is a pure loss.
- **Mention resolution** (handle/name → participant id, in `AgentTools` and
  `runtime/tools.py`). It reads like protocol but is not: it needs the
  participant roster, which is REST-derived SDK state, and its fallback rules are
  product behaviour rather than wire contract. This is precisely the "feels like
  a wrapper" boundary — leave it in each SDK.
- **REST endpoint construction** (`endpoints.rs`). Covered by §6: codegen from
  the OpenAPI spec is the better single source of truth.

### What this means for sequencing

The payload models, event vocabulary, disconnect taxonomy, and topic builders are
**pure data and pure functions** — no session state, no driver loop. They can
cross the FFI boundary before `Session` does, they are individually verifiable
against the current hand-written models, and they retire most of the drift above.
That makes them the natural content of Phase 1, which otherwise only proves the
packaging pipeline.

---

## 5. Three transports is the right answer

With the native Rust client confirmed, transport will exist three times: Rust,
Python, TypeScript. That is the correct outcome, not a concession.

The trade to keep in view is **three thin drivers over one shared brain** versus
**three brains**. Today it is three brains — `core-protocol`'s logic exists in
parallel hand-written form in `link.py`/`streaming/client.py` and in
`PhoenixChannelsTransport.ts`/`disconnectReason.ts`. That is the actual problem.
Three drivers dispatching over a four-variant effect enum is a rounding error by
comparison, and each one buys back native debuggability, native testing, native
logging, and native cancellation in its own language.

The native client changes the justification for `core-transport` decisively: it
is no longer speculative infrastructure, it is that client's transport, written
in its own language, with a first-class consumer. It simply should not also be
what the SDK bindings export.

### How you keep three drivers honest

Not by sharing their code — by sharing their **test corpus**. Record scenario
traces (input sequence + clock) and assert the resulting effect sequence, then
run the same corpus against all three drivers. A driver that mishandles a
join-timeout-during-catch-up fails the same recorded case in every language.

This is a cheaper and more durable single source of truth for driver behaviour
than an FFI data path, and `band-sdk-python` already uses the pattern — its
framework-conformance suite holds every adapter to one shared contract.

---

## 6. REST should not cross the boundary at all

This is the single largest reclaimable investment in the repo: ~3,000 of
`core-transport`'s 4,831 lines, and ~80% of both bindings.

**The drift argument does not apply here.** REST is stateless request/response
with no subtle shared logic. And the duplication problem is already solved by a
stronger mechanism: `band-client-rest` is Fern-generated from the OpenAPI spec.
A spec that generates both clients is a better single source of truth than a
hand-written Rust client that must be kept in step with the spec by hand.

**The cost is concrete.** Routing REST through Rust takes away users' `httpx` /
`fetch` interceptors, proxy configuration, retry policies, custom timeouts, and
mock transports — the ordinary tools people reach for when integrating an SDK
into an existing service. That is the "feels like a wrapper" failure in its most
literal form, on the surface users touch most often.

Keep the Rust REST client for the native Rust client, which needs one. Do not
export it through the bindings.

---

## 7. Binding strategy

**Python — PyO3 + maturin, narrow surface.** Export `Session`, the frame codec,
the backoff planner, the disconnect taxonomy, topics, and the rejoin policy.
Small, synchronous, no async bridging: because only protocol decisions cross, the
asyncio-to-Tokio bridging problem the proposal flagged largely evaporates. There
is no Rust runtime to own.

**TypeScript — wasm, not NAPI.** `core-protocol` already passes a
`wasm32-unknown-unknown` check build in CI, gated precisely so no socket, TLS or
Tokio dependency can creep in. That gate is a strategic asset worth cashing:

- no per-platform prebuild matrix, no native addon per OS × arch × Node ABI
- works in bundlers, edge runtimes, serverless, and browsers
- resolves the proposal's open question ("napi-rs or wasm, depending on whether
  the TS SDK has to run in a browser") in the direction that keeps the option
  open rather than closing it

The TS SDK is already structured for this: `createNodeWebSocketFactory` returns
a standard `typeof WebSocket`, so the Node-specific piece is already isolated
behind the platform interface. A wasm protocol core plus the existing socket
factory is a natural fit; a NAPI addon would tie the SDK to native binaries
permanently and foreclose the browser path.

Note that NAPI would be the right call if transport crossed the boundary — wasm
cannot open a socket. The binding-strategy question and the where-the-line-sits
question are the same question. Answering "protocol only" is what makes wasm
available.

**Potential Go support would take wasm too, for a different reason.** Go has no
binary distribution channel at all — modules ship source — so wasm plus a
pure-Go runtime removes an artifact matrix where a PyO3-style native binding
would introduce one. Nothing below changes if a Go SDK appears; see
[Appendix A](#appendix-a-what-a-go-sdk-would-change).

### Wasm is not better than NAPI on all fronts

It is worth being explicit about where NAPI genuinely wins, because the
recommendation rests on *which* fronts those are, not on wasm dominating.

NAPI beats wasm on:

1. **OS access.** `wasm32-unknown-unknown` has no sockets, no TLS, no
   filesystem, no clock. WASI covers some of that, but WASI sockets are
   immature and are not what Node's `WebAssembly` API provides.
2. **Threads and async.** NAPI runs a real multi-threaded Tokio runtime with
   first-class async. Wasm is single-threaded absent `SharedArrayBuffer` +
   COOP/COEP, and its calls are synchronous — async needs the host to pump it.
3. **Zero-copy.** NAPI can hand JS a `Buffer` backed by Rust memory. Everything
   crossing wasm's boundary is a copy through linear memory, plus UTF-8↔UTF-16
   conversion for strings. (True, but the smallest term of the three that
   matter — measured below.)
4. **Raw speed** — typically 1.2–2× native for compute.
5. **The Tokio ecosystem.** `tokio-tungstenite` does not compile to wasm at
   all; `reqwest`'s wasm backend only maps to `fetch`.
6. **Debugging and profiling** — native stack traces, `perf`, real profilers.

Now read that list against `core-protocol`: no sockets, no TLS, no threads, no
async, no Tokio, and — per its own module doc — no clocks and no randomness
either, since time enters as `Millis` parameters and randomness as injected
closures.

**Every front NAPI wins on is a front `core-protocol` does not compete on.**
Conversely, `core-transport` is exactly the shape wasm handles worst. Two
independent lines of reasoning — this one and §2's — land on the same crate
boundary, which is reasonable evidence it is the right one.

The one NAPI advantage that plausibly applies is boundary copy cost. That one
is worth measuring rather than assuming, because the intuition behind it turns
out to be backwards in the size range Band actually operates in.

### The copy cost, measured

Both boundaries were built against `core-protocol`'s real decode path —
`serde_json` into the 5-element array, `FrameView` handed back out — and driven
from Node 26 on arm64 with representative Band frames. Every figure is scored
against V8's own `JSON.parse` of the same frame, which is what the TS SDK pays
today with no Rust in the picture.

**The copy is the smallest term in the boundary.**

| Frame | `JSON.parse` (baseline) | wasm copy-in, bytes | wasm copy-in, string |
|---|---|---|---|
| 63 B heartbeat reply | 246 ns | 52 ns (0.21×) | 104 ns (0.42×) |
| 853 B chat message | 1.2 µs | 61 ns (0.05×) | 132 ns (0.11×) |
| 4.7 KB agent reply | 2.4 µs | 140 ns (0.06×) | 308 ns (0.13×) |
| 40.7 KB tool_result | 13.3 µs | 758 ns (0.06×) | 1.4 µs (0.11×) |

Copying a whole 40 KB frame into linear memory costs 758 ns — roughly 54 GB/s,
and 6% of what V8 spends parsing that same frame. Everything NAPI's zero-copy
could save lives inside that column, and a bare NAPI call already costs 40 ns.
The ceiling on the advantage is under a microsecond at the largest frame the
platform sends.

**What actually costs something is re-materializing the payload on the far
side — and there NAPI is not uniformly better.**

| Frame | wasm → JSON → `JSON.parse` | NAPI buffer → JS object | NAPI → JSON → `JSON.parse` |
|---|---|---|---|
| 63 B | 4.19× | 5.61× | 3.60× |
| 853 B | 3.58× | **5.32×** | 3.45× |
| 4.7 KB | 3.11× | 2.95× | 3.02× |
| 40.7 KB | 2.88× | **1.06×** | 2.66× |

NAPI builds a JS object node by node through V8, so it pays per *node*; wasm
copies bytes, so it pays per *byte*. The 853 B and 40 KB fixtures carry the same
node count and differ only in one string's length, which isolates it — NAPI's
fixed cost is ~6.5 µs either way. The crossover sits around 3–4 KB. Below it,
where most of Band's traffic lives and where payloads are structurally rich with
`mentions` and `metadata`, NAPI's "zero-copy" path runs **1.5× slower than
wasm**. Above it NAPI wins clearly.

Hand the same JSON string back from either technology, though, and they land
within 10% of each other at every size. **The boundary's shape dominates the
boundary's technology by 3–6×.** That is the real finding: this was never a
wasm-versus-NAPI question.

**Keeping the payload out of Rust erases the question entirely.** Host parses
the frame, only `topic` and `event` cross, a routing code comes back:

| Frame | wasm | NAPI |
|---|---|---|
| 63 B | 1.65× | 1.53× |
| 853 B | 1.15× | 1.14× |
| 4.7 KB | 1.06× | 1.07× |
| 40.7 KB | 1.02× | 1.02× |

Identical within noise, and near-free. That is the boundary §2 already argues
for on other grounds — "message data never leaves the host runtime" — and it
turns out to be both the fastest option and the one that makes the binding
technology performance-neutral.

**The design consequence.** `FrameView` is what would drag payloads across: its
own doc comment says it exists for "a driver handing a decoded domain event to a
caller across a non-Rust boundary," and it carries `payload: Value`. That is
precisely the 3× path. Under an envelope-only boundary the payload stays a
host-side value and only the classification crosses, so `Frame::decode` runs
host-side and `FrameView` is not part of the binding surface at all. This also
settles a small ambiguity in §1: "message payloads never touch this machine" is
true of `Session`, but the frame codec is listed as part of the binding surface
and it does touch them.

**None of this is load-bearing.** The slowest path measured — NAPI object
construction at 853 B, 6.5 µs — sustains ~154,000 frames/s on one core. Band
runs at human chat rates, and the largest burst is a reconnect's join acks,
capped by `DEFAULT_WATCH_CAP` at 48 control frames, ~50 µs in total. There is
roughly four orders of magnitude of headroom on every option. Decide this on
§2's grounds; the performance column is not close enough to anything to matter.

Caveats: the wasm side used a raw pointer ABI, so real `wasm-bindgen` glue adds
tens of nanoseconds per call, which moves no conclusion; warm JIT, in-cache
data, single-threaded throughout.

### Runtime reality

Wasm is near-universal in JS, with one real gap:

| Runtime | Wasm | Native addon |
|---|---|---|
| Node, Deno, Bun, Electron, all browsers | yes | Node/Electron only |
| Cloudflare Workers, Vercel Edge | yes, modules bound at deploy | no |
| React Native (Hermes) | **no** | n/a |

**Which Hermes.** Every "Hermes" in this document is
[Hermes](https://github.com/facebook/hermes), Meta's ahead-of-time-compiled
JavaScript engine and React Native's default runtime — the one with no
`WebAssembly` object. It is unrelated to
[Hermes Agent](https://github.com/NousResearch/hermes-agent), Nous Research's
open-source agent harness and CLI.

Two ergonomic wrinkles: `WebAssembly.instantiate` is async, so the SDK likely
needs an `await init()` in its setup path (unrestricted sync instantiation
exists in Node but not browsers); and wasm-bindgen emits different JS glue per
target (`nodejs` / `bundler` / `web`). That is a far milder matrix than NAPI's —
two or three text files, not ten binaries.

Relevant detail: `bindings/node/package.json`'s `napi` config declares no
`triples`, so `napi build --platform` currently builds host-only. The
multi-platform matrix pain is ahead of this project, not behind it.

### Wasm is not the answer for Python

It is possible — `wasmtime-py` is the maintained runtime — and it is the wrong
call, for a reason that is easy to miss: **`wasmtime-py` is itself a compiled
extension with its own per-platform wheels.** The wheel matrix is not escaped,
only deepened: `Python → wasmtime native ext → wasm → Rust` instead of
`Python → PyO3 ext → Rust`. Same distribution burden, one more boundary, two
copies instead of one, and all marshalling hand-written, because wasm offers
integers and linear memory where PyO3 offers real Python classes, type stubs,
and exceptions that map to Python exceptions.

The single thing wasm buys in JS — dodging the per-platform artifact — buys
nothing in Python. So **wasm for TypeScript, PyO3 for Python** is not an
inconsistency; the asymmetry is the point. Wasm solves a problem the JS
ecosystem has and Python does not.

(Speculative exception: **Pyodide**, Python compiled *to* wasm for the browser.
If running a Band agent under Pyodide ever matters, so would a wasm or
pure-Python protocol path. Not now.)

### Async bridging: the data path evaporates, the diagnostics path does not

The proposal names async bridging into asyncio and the Node event loop as a
decision to settle. §2's "Two I/O runtimes in one process" is what that costs
when it has to be built; this is why, under the narrow line, it mostly does not.
The question splits into two paths with opposite answers, and it is worth
separating them because they look like one problem.

**The data path evaporates.** `apply(input, now) -> [effects]` is synchronous and
pure. The host calls it on its own thread and gets a `Vec` back. There is no Rust
runtime, no task to cancel, no thread to join, and nothing to schedule onto an
event loop. Every hard question — how `asyncio.CancelledError` reaches a Tokio
task, what happens to in-flight work at shutdown, how backpressure is signalled —
simply does not arise, because message data never crosses.

**The diagnostics path does not — unless the protocol layer stops logging.**
Logs are the one thing that would otherwise *have* to travel from Rust to the
host, since their destination is the host's logger. As a per-event crossing from
arbitrary Rust threads that needs the full apparatus: a bounded queue, a
drop-on-full policy, and a host-side drain. On Node that is `ThreadsafeFunction`;
on Python the blocker is the GIL rather than the event loop, and taking it inline
in `on_event` would let a busy interpreter stall transport threads. §12 works
that through against the bindings as they stand.

§3 removes this path too, by keeping `core-protocol` free of `tracing` and having
it **return** diagnostics in the value the host already receives. A fact that
rides an existing return needs no bridge, no queue, and no drop policy — and the
host logs it natively rather than through a shim.

So the honest answer to the scope question is: **under the narrow line, plus
diagnostics-as-data, there is no async bridge left to build for the SDKs at
all.** The data path never crosses because the host owns the socket; the
diagnostics path never crosses because the protocol layer returns facts instead
of emitting them. The `tracing` bridge stays relevant only for a host that binds
something which genuinely logs — `core-transport` — which under this
recommendation nothing does. The native Rust client uses `tracing` directly, with
no boundary in the way.

That is a stronger result than the proposal's framing anticipated, and it is
worth being precise about *why*: it is not that async bridging was overrated, it
is that both things that would have needed it were moved to the other side of the
boundary.

### Errors: map into each SDK's hierarchy, not beside it

Both bindings today collapse the entire taxonomy into one flat exception:

```rust
// bindings/python/src/pyerr.rs        // bindings/node/src/error.rs
PyRuntimeError::new_err(e.to_string()) // napi::Error::from_reason(e.to_string())
```

`TransportError` has eight variants, and the distinctions between them are
exactly the ones §4 showed are operationally load-bearing: `Suppressed` and
`Superseded` are terminal, `Disconnected` may still reconnect, `UpgradeRejected`
carries a retryability verdict, and `Api` carries a `request_id` for correlating
against backend logs. Flattened to a string, a caller can only recover any of
that by matching on message text.

That is acceptable while the bindings have no consumers. It is not acceptable
once an SDK depends on them, and it gets worse under this recommendation rather
than better — with the host owning transport, the *host* is what has to decide
whether to reconnect.

Three rules:

1. **Preserve the discriminant.** Whatever crosses must let a caller branch on
   which failure occurred without parsing a message. On Python, a small exception
   subclass per group; on TypeScript, a discriminated union or a `code` field —
   the same shape `disconnectReason.ts` already uses.
2. **Land inside the SDK's own hierarchy, not beside it.** `band-sdk-python`
   already has `BandError` → `BandConfigError` / `BandConnectionError` /
   `BandToolError`. A core failure should arrive as a `BandConnectionError`
   subclass so existing `except BandError` handlers keep working. A bare
   `RuntimeError` from a Rust extension is the "wrapper around a foreign library"
   tell, in the place users meet it most.
3. **Keep the rich taxonomy per-SDK, and keep the *facts* shared.** Retryable vs
   terminal is a protocol fact and belongs in `core-protocol`. How it is spelled
   — exception class, error code, union tag — is idiomatic and belongs to each
   SDK. Share the verdict, not the vocabulary.

Under the narrow line the protocol surface itself raises very little — malformed
frame, invalid state transition — so this is mostly about the errors each SDK's
own transport raises, informed by a shared classification.

---

## 8. Packaging, release, and versioning

The narrow line is what keeps this tractable, so it is worth stating what it
buys:

- **Python is pure-Python today.** Adding a compiled extension means wheels
  across CPython versions × platforms × glibc/musl × arm64/x86_64, plus
  free-threaded builds. That tax is worth paying for 2,153 lines of
  irreplaceable state machine. It is not worth paying for a generated REST
  client — and the narrow surface is small enough that an ABI3 wheel is
  realistic, collapsing the CPython-version axis for the default GIL build.
- **TypeScript pays nothing** on the wasm route: one artifact, no matrix.
- **Version the protocol crate separately from the SDKs.** They move at
  different rates for different reasons — `band-sdk-python` is at 1.6.0 after
  nine months while `core` is at 0.2.0 after eleven days. Pin the core exactly
  in each SDK, as `band-sdk-python` already does with `band-client-rest`.

**Sequence the release work before the migration work.** The proposal is right
that this is where these projects stall, and the failure mode is discovering the
wheel matrix after the code is written. Prove an end-to-end wheel and npm
publish with a trivial protocol export first, then migrate behaviour into it.

### The CI matrix is currently one cell

Worth stating plainly, because "we have CI for the bindings" is easy to mistake
for "we have a release matrix." All three jobs — `rust`, `bindings-python`,
`bindings-node` — run on `ubuntu-latest` and nothing else. There is no macOS, no
Windows, no arm64, no musl, no CPython version matrix, and Node is pinned to a
single version — and one its only consumer does not support (§12).

That is entirely reasonable for a repo with no consumers — it proves the code
compiles and the tests pass. It is not a release matrix, and the gap between the
two is the work the proposal warns is underestimated. What the current CI
demonstrates is *correctness*; what a release needs to demonstrate is
*buildability everywhere the SDKs are installed*, which for `band-sdk-python`
today means every platform a pure-Python wheel silently supported.

Concretely, adopting the core turns one universal `band-sdk` wheel into a build
matrix. The ABI3 recommendation above collapses the CPython axis; the platform
axis remains and has to be built out before Phase 3, not after. This is the
strongest practical reason Phase 1 exists.

### How a core fix reaches a user

The proposal asks how the artifacts ship relative to one another, and the answer
is a chain worth seeing written down:

```text
core fix merged → core release (release-please)
  → wheel + npm published
    → dependency bump in band-sdk-python / band-sdk-typescript
      → SDK release (each repo's own release-please)
        → user upgrades
```

Five hops, across four repos with four independent release trains. That is the
real cost of a shared core, and it is worth accepting deliberately rather than
discovering during an incident. Two consequences:

- **A protocol hotfix is not fast.** Today a reconnect bug in `band-sdk-python`
  is one PR and one release. Afterwards it is a core PR, a core release, a bump,
  and an SDK release. Decide in advance whether that is acceptable for a Sev-1,
  and if not, keep an escape hatch — an SDK-side override for the affected
  decision — rather than discovering the need mid-incident.
- **Automate the bump.** Dependabot already opens these PRs in both SDK repos and
  `band-sdk-python` already pins `band-client-rest` exactly, so the mechanism
  exists and is proven. Use it rather than inventing cross-repo release
  orchestration.

### One versioning decision to make at Phase 2, not now

`band-sdk-core`'s release-please config is `release-type: simple` with a **single
version applied to all five manifests** — the three crates plus both bindings.
Every release bumps everything in lockstep.

That is right today and becomes friction later. Under this recommendation the
SDKs consume `core-protocol` while only the native client consumes
`core-transport` — so once the SDKs pin the core exactly, a transport-only fix
for the native client still churns their pinned dependency and invites a bump PR
for a change that cannot affect them.

Two options, both defensible: split the release so `core-protocol` versions
independently, or keep the unified version and accept some no-op bumps. Unified
is simpler and the churn is cosmetic, so I would keep it and revisit only if the
noise becomes real. Flagging it because it is invisible until the first SDK pins
the core, and much cheaper to change before that than after.

---

## 9. Maintenance and ownership

The proposal names this as a team question, and the repo state makes it concrete:
53 commits, 42 from a single author, zero pull requests, eleven days old.

**How to read this section.** The SDKs are maintained by a single developer
today, and none of what follows is a criticism of that — a small team is part of
why the repo is as coherent as it is. But the maintenance questions a shared
core raises outlast whoever is answering them, and the team is far more likely
to grow than to shrink. So the concerns below are stated in general terms, for
the team the core will have rather than the one it has now.

A shared core means every SDK bug is now potentially a Rust bug — and the number
of people who can fix it is currently one. Two consequences:

- **The narrow line limits the blast radius.** If only protocol logic is shared,
  a Python transport bug stays a Python bug, fixable by a Python engineer. If
  transport is shared, every socket bug in every SDK becomes a Rust bug behind a
  release-and-republish cycle.
- **A one-time review does not fix the bus factor.** This document is the
  second pair of eyes the repo was missing, so that gap is closed rather than
  outstanding — the repo is squash-merge-only with a clean Conventional-Commits
  history and good CI, and the architecture holds up. What a review cannot
  supply is a *standing* reviewer. Once an SDK pins the core, every protocol fix
  needs someone besides its author who can review it under time pressure, and
  that person is still unnamed. It is the "who is on call" question below.

### The team question, concretely

The proposal frames this as a team question as much as a technical one, so it is
worth making the technical part of it explicit — these are the things that decide
whether "every SDK bug is now a Rust bug" is survivable.

**Can a Python engineer reproduce a core bug?** Today, debugging into the core
means a Rust toolchain, `maturin develop`, and reading Rust. That is a real step
up from `pip install -e .`. Two things make it tractable, and both are cheap if
done early:

- Keep the sans-io core **drivable from a test in each host language**. If a
  Python engineer can feed `Session` a sequence of inputs from pytest and assert
  the effects, most protocol bugs are reproducible without touching Rust at all.
  This falls out of the narrow line for free — it is the same property that makes
  shadow mode (Phase 2) possible.
- Document the one-command path from a clean checkout to a locally built wheel.
  `just dev-python` already exists; what is missing is it being the documented
  entry point for an SDK engineer rather than a core maintainer.

**Who is on call for a core bug?** Worth an explicit answer before the SDKs
depend on it, because the honest current answer is one person, and the escalation
path when they are unavailable is undefined. The narrow line is itself the main
mitigation — it keeps sockets, retries, and framing in each SDK's own language,
so the population of bugs that *must* be fixed in Rust stays small.

**What is the fallback if a core bug blocks a release?** With the narrow line
there is a real one, and it should be written down rather than improvised: the
host owns the driver, so an SDK can override a specific protocol decision locally
— pin a backoff delay, treat a close code as terminal — and ship, while the
proper fix goes through the core. That escape hatch only exists because the SDKs
keep their own transport. It is worth naming as a benefit of the line, not just a
contingency.

**Skills, going forward.** A shared core means the team needs at least two people
who can review Rust, and it means SDK engineers need to be able to *read* it even
if they do not write it. That is a hiring and onboarding input, not just an
architectural one, and it is the part of this decision that is hardest to reverse
later.

---

## 10. Migration, keeping both SDKs shipping

**Phase 0 — Fix the line.** Stop growing the REST binding. Then the fold (§3):
make `dispatch_frame`, `disconnect_outcome`, and `fail_pending` pure; move the
~520 lines of policy out of `socket.rs` into `core-protocol`; fold `room_watch`
in alongside them and dissolve `core-runtime`. Pure refactoring — no SDK changes,
no behaviour change, and `core-protocol`'s existing wasm32 gate verifies the
result mechanically. *This is the cheapest it will ever be: the binding surface
is eleven days old and has zero consumers.*

**Phase 1 — Prove the pipeline.** Export a trivial slice of `core-protocol`
(topics, the disconnect taxonomy) through PyO3 and wasm. Ship a real wheel and a
real npm artifact end to end. No behaviour changes. This de-risks packaging
before any logic depends on it.

**Phase 2 — Shadow mode in Python.** Expose `Session` and the backoff planner.
Keep the existing transport fully in charge, but have it also feed the Rust
machine and log any divergence between the two. Nothing user-visible ships.
This is what makes the migration safe: divergences surface in production traffic
before anything depends on them.

**Phase 3 — Cut Python over.** Make the Rust machine the authority; delete the
duplicated decision logic from `link.py` and `streaming/client.py`. The SDK
keeps its own sockets, its own asyncio, its own logging, its own test fakes.

**Phase 4 — TypeScript, same path** via wasm, reusing the shadow-mode approach.

**Phase 5 — Native client** consumes `core-transport` directly. Promote the
scenario corpus (§5) to a shared gate across all three drivers.

Each phase ships independently and leaves both SDKs releasable. Phases 0 and 1
are the ones worth starting now, because both get more expensive with every week
of binding growth.

---

## 11. What we should avoid: a summary

**Nothing below is new.** This is a recap: the decisions argued across §1–§10,
restated as the failure each produces if taken the other way, so the whole set
can be scanned in one place instead of reassembled from ten sections. Every item
names where its case is made.

1. **Exporting the REST client through the bindings**
   ([§6](#6-rest-should-not-cross-the-boundary-at-all)). Codegen from the
   OpenAPI spec is the better single source of truth, and this is where users
   most expect native ergonomics.
2. **Putting message payloads on the FFI path**
   ([§2](#2-why-this-line-specifically), and measured in
   [§7](#7-binding-strategy)). The moment room messages cross the boundary, you
   inherit queueing, backpressure, drop policy and cross-runtime cancellation —
   permanently.
3. **Letting Rust own the SDKs' sockets**
   ([§2](#2-why-this-line-specifically)). It puts a second I/O runtime in the
   host's process permanently — two schedulers contending over one GIL or one
   loop thread, two cancellation models, two shutdowns, and a seam neither
   ecosystem's tooling can see into. It costs test fakes and host-native
   observability, and buys deduplication of the cheap half of the problem.
4. **Choosing NAPI for TypeScript** ([§7](#7-binding-strategy)). It forecloses
   the browser and edge paths that the existing wasm32 gate keeps open, and
   buys advantages (OS access, threads, zero-copy) that `core-protocol` cannot
   use.
5. **Letting `core-transport`'s DTOs become the SDKs' public types.** The one
   item here not argued at length above — it applies to wire models the same
   principle [§4](#4-what-the-sdks-already-re-implement) applies to mention
   resolution and [§7](#7-binding-strategy) to errors. Each SDK already has
   idiomatic models, Pydantic on one side and its own types on the other, and
   wire DTOs leaking into public API is how an SDK starts feeling like a
   foreign library.
6. **Adding a crate per conceptual layer**
   ([§3](#3-the-line-is-currently-drawn-too-low)). The sans-io/I/O split is the
   only boundary that pays for itself — it is enforceable by CI and it is where
   the bindings attach. Layers below that are modules. `core-runtime` is the
   warning: 186 lines, existing mostly so two bindings can share one constant,
   and now the obstacle to putting related pure logic in one place.
7. **Making the compiled core a hard dependency of `band-sdk` before the wheel
   matrix is proven** on every platform the SDK currently supports
   ([§8](#8-packaging-release-and-versioning)).
8. **Versioning the core in lockstep with the SDKs**
   ([§8](#8-packaging-release-and-versioning)). They move at different rates;
   pin exactly instead.
9. **Building on a repo with a bus factor of one**
   ([§9](#9-maintenance-and-ownership)). This review closes the unreviewed
   half; a standing second reviewer is the half still unnamed. Fixable now,
   expensive later.

---

## 12. Issues to address in `band-sdk-core`

Four defects worth fixing regardless of where the line lands. The first two are
also the sharpest illustration of §3's argument: under the narrow line plus
diagnostics-as-data, they do not get built out — they cease to exist.

### The two bindings expose tracing in opposite shapes, and Python's is the wrong one

| | `bindings/node` | `bindings/python` |
|---|---|---|
| API | `initTracing(callback)` | `Tracing()` |
| What it installs | a layer forwarding each event to JS | `tracing_subscriber::fmt` |
| Where a log line ends up | whatever logger the host app already uses | Rust's own stdout |
| Filtered by | the host | `RUST_LOG` |
| Crossings | one per event, for the process lifetime | one, at setup |

Both are FFI. What separates them is **where the boundary sits relative to the
log data**. `Tracing().__aenter__()` is a one-shot switch: after it returns,
Rust does its own formatting, filtering and writing on whatever thread emitted
the event, and no log line is ever handed to Python. Node's events have to reach
JS to get anywhere, and that per-event data path is what forces every
complication in `bindings/node/src/tracing_init.rs` — the `ThreadsafeFunction`,
the 1024-slot queue with drop-on-full, and the `Weak = true` that §2 already
uses as its preview of the problem, plus an `on_event` kept trivially
panic-free, since `catch_unwind` guards the exported functions but not a layer
running on some arbitrary thread. Each is a consequence, not a preference.

**The destination is the part that matters.** §2 states the consequence — a
Rust-owned transport puts the SDK's most important subsystem outside
`BAND_LOG_*` — and this is the mechanism under it. `band-sdk-python` already has
a complete logging story: stdlib `logging` configured through `BAND_LOG_*`
(`src/band/config/logs.py` — level, root level, file sink, rotation, per-logger
overrides), plus a documented "host owns the telemetry pipeline" stance for
OpenTelemetry. Adopt the core as it stands and a Python operator gets **two
disjoint logging systems**: two environment variables that do not know about
each other (`RUST_LOG` versus `BAND_LOG_LEVEL`), Rust output that bypasses the
configured file sink and rotation entirely, and no way to route core
diagnostics through the same handlers as the rest of the SDK's logs. Same file
descriptor, so in a terminal it *looks* integrated — but there is no shared
ordering, no shared formatting, and none of the host's filters apply.

On this one axis the Python binding is *behind* Node, despite being well ahead
of it on REST and WebSocket coverage.

**The fix, and the condition on it.** If `core-transport` stays exposed through
a binding, give Python the Node shape: a callback bridge handing each event to
Python so the SDK can route it into `logging` and `BAND_LOG_*` stays the single
control surface, keeping the current `fmt`-to-stdout mode as an opt-in
convenience for standalone Rust use. That signs up for the per-event crossing,
so backpressure has to be answered on the Python side too — the same question
with a different blocker. Events fire on arbitrary Tokio threads, and reaching a
Python callable means `Python::attach`, so taking the GIL inline in `on_event`
would let a busy interpreter stall transport threads. The answer is the one
Node's `NonBlocking` mode already encodes: a bounded queue with drop-on-full,
drained by a Python-side task, rather than an inline attach.

**Under the recommendation it is resolved by design instead.** If the SDKs bind
only `core-protocol`, and that crate returns diagnostics as data rather than
logging them (§3), the protocol layer never emits a log line and there is
nothing to bridge. The native Rust client uses `tracing` directly, with no
boundary in the way. So: build the bridge if `core-transport` stays bound, and
drop the question if it does not. Either way the cost above is the reason
diagnostics-as-data is worth the return-type plumbing.

### No spans exist yet, and Node's binding has machinery for ones never written

There are no spans anywhere in the core crates — no `#[instrument]`, no
`info_span!`. All 21 `tracing` call sites are flat events, 19 of them in
`core-transport/src/socket.rs` and mostly `warn!`. Two consequences:

- The Node layer's span machinery — `on_new_span`, per-span field storage,
  `event_scope` flattening with innermost-wins — currently flattens nothing.
- Its doc comment names `correlation_id`, `room_id` and `message_id` as the span
  fields it merges. Today `correlation_id` appears only as an *event* field at a
  single site, and as a protocol payload field. The comment reads as a
  description of current behaviour and is not one.

§2 uses the small version of this as evidence for how thin the cross-runtime
record already is. The local choice is to instrument the transport with real
spans — a connection-scoped or room-scoped span is the obvious first one, and
would make the flattening pay off immediately — or to reword the comment to say
what it is: machinery built ahead of the instrumentation it is for.

The narrow line adds a third option and is probably the answer. The span
machinery lives in `bindings/node`, the layer whose only purpose is carrying
`core-transport`'s logs to a JS host. If the SDKs bind only `core-protocol` it
has no consumer at all, and "instrument or reword" becomes "delete". Spans in
`core-transport` itself stay worthwhile either way: the native Rust client is a
real consumer, and a connection-scoped span is exactly what one wants when
debugging a reconnect storm from the Rust side.

### The Node addon is tested on a runtime its consumer does not support

`ci.yml` and `e2e-live.yml` both pin `node-version: "20"`. Node 20 being past
end of life is the visible problem; the mismatch is the sharper one.

| Where | Node version |
|---|---|
| `band-sdk-core` CI (both workflows) | 20 |
| `band-sdk-typescript` (`engines.node`) | `>=22.14.0` |
| `bindings/node/package.json` | **no `engines` field at all** |

The addon is built and smoke-tested on a runtime the only SDK meant to consume
it does not support, and nothing in the binding's own manifest declares a floor.
A NAPI addon compiled and validated against Node 20's ABI is not evidence that
it works on the version its consumer will actually run. This is §8's one-cell CI
matrix in its most concrete form.

Three steps:

1. Move both workflows to a supported LTS. Matching `band-sdk-typescript`'s
   22.14 floor is the minimum; testing the current LTS as well is better, since
   a native addon's whole risk surface is ABI compatibility.
2. Add an `engines` field to `bindings/node/package.json`, so the floor is
   declared where a consumer can see it rather than living only in CI.
3. Keep the two in sync deliberately. The binding's floor and the SDK's floor
   are one decision, and today they are two independent silences.

The wasm route (§7) makes this moot for the SDK — wasm has no ABI coupling to a
Node version — but it still applies to any native addon that ships, and to CI's
ability to catch a regression at all.

### The justfile: three small gaps

The justfile is a good choice and its layout is sound. Three things would make
it do its job better. The first two are trivial; the third is the one that
actually costs something.

**Bare `just` does not list the targets.** With no arguments `just` runs the
first recipe in the file, currently `doctor`, so a newcomer typing `just` gets a
toolchain check rather than a menu. The convention is a first recipe that lists:

```just
# List available recipes.
default:
    @just --list
```

**`just doctor` reports but cannot repair.** It checks for `rustc`, `cargo`,
`cargo-nextest`, `cargo-llvm-cov`, `cargo-deny` and `cargo-audit`, then leaves
the reader to install whatever is missing by hand. A `--fix` mode that installs
them — they are all `cargo install` or `taiki-e/install-action` equivalents —
turns a diagnostic into onboarding. §9 asks whether a Python engineer can
reproduce a core bug; this is a direct input to that answer.

**CI duplicates the recipes by hand.** The justfile's own header says so:

> `ci.yml` duplicates these commands by hand and must be kept in sync when a
> recipe changes; `e2e-live.yml` instead installs just and calls the e2e-rust
> recipe directly, so that lane never drifts.

The comment is honest about the hazard without removing it. A recipe and its CI
twin can drift silently, and the failure mode is CI passing on something the
local command no longer does, or the reverse. The fix is already demonstrated in
the same repo: adopting `e2e-live.yml`'s pattern in `ci.yml` costs one setup
step per job and retires the sync burden entirely. If there is a reason to keep
CI free of `just` that I have not seen — a runner constraint, or wanting CI
readable without knowing just's syntax — the alternative is to make drift
*detectable*: run the recipes through `just --dry-run` in CI and diff against
the inlined commands. But installing `just` is simpler, and one lane already
does it.

---

## Settled

- **Mobile / React Native is not designed for upfront** (Gavrie, 17 Aug 2026).
  Hermes — React Native's JavaScript engine, not the agent of the same name —
  has no wasm support, which is the one runtime gap in §7. The decision is to
  YAGNI it and go native if and when it becomes real.

  This is a safe deferral specifically *because* `core-protocol` is sans-io:
  "go native later" is not a rewrite but a second build target on unchanged
  source. The same crate compiles to `wasm32` and to a NAPI addon; only the
  driver differs, and under this recommendation the driver is per-host anyway.
  The cost of deferring is roughly a CI job and a packaging decision, paid when
  a Hermes runtime actually appears.

  **Trigger to revisit:** a committed mobile or React Native client.

### The two decisions have very different reversibility

Worth keeping separate, since they are easily conflated:

| Decision | Nature | Cost to reverse |
|---|---|---|
| wasm vs NAPI | a build target | Cheap — add a target, unchanged source |
| Where the FFI line sits | the binding surface, both SDKs' internals, whether the SDKs run one runtime or two, whether users can fake transport | Expensive, and compounds weekly with binding growth |

The first can wait for evidence. The second is the live decision.

---

## Open questions

- **Does the TypeScript SDK need to run in a browser?** The answer does not
  change the recommendation — wasm is the better fit either way — but it changes
  how firmly to close the NAPI door.
- **Does the native client share the SDKs' semantics, or only the wire
  protocol?** If it also wants rooms, tools, and adapters, `core-runtime` will
  grow, and that growth needs the same line-drawing discipline applied to it.
- **What is `band-mcp`'s relationship to the core?** It speaks the same
  protocol; whether it becomes a fourth consumer affects how general the shared
  surface should be.

---

## Appendix A: What a Go SDK would change

*Not on the roadmap. Recorded because the answer turned out to be short, and
because it independently tests the line §2 draws.*

**Nothing in the recommendation changes**, and Go is the strongest
platform-specific evidence for it. Everything Go is chosen for —
`CGO_ENABLED=0`, one static binary, `GOOS=linux GOARCH=arm64 go build` simply
working — is exactly what a transport-crossing boundary destroys. Three rows of
§2's table get sharper rather than weaker:

- **Cancellation.** In Python, an `asyncio.CancelledError` that never reaches a
  Tokio task is a bug. In Go, `context.Context` propagation is not a
  convenience but the convention every library obeys, so an SDK whose socket
  cannot see `ctx.Done()` does not read as a Go library at all.
- **Observability.** pprof, the race detector, the execution tracer and
  `log/slog` all stop at the FFI boundary, the way `tracing` does not reach
  Python's `logging`.
- **Test fakes.** `net.Pipe()` and interface-typed transports are how Go SDKs
  are tested; a Rust-owned socket is opaque to all of it.

§5 would also become four drivers, which promotes the shared scenario corpus
from a good idea to a prerequisite — four-way parity is not verifiable by
reading.

### cgo in 2026: fast now, still the wrong default

The reputation is half out of date. Go 1.26 (February 2026) removed the
`_Psyscall` processor state, cutting baseline cgo call overhead from
[50.1 ns to 26.5 ns](https://gist.github.com/DeedleFake/2f50b02c0708484c66d18253302c4fd6)
— faster than the 40 ns bare NAPI call measured in §7. Speed is no longer an
argument against cgo, and per §7's own finding it was never the deciding column.

What did not improve, and will not:

| | Why it is structural |
|---|---|
| **Cross-compilation** | cgo on means a C cross-toolchain plus a Rust `staticlib` per target triple |
| **Distribution** | Go modules ship *source*. No wheel, no npm binary channel, no install hook |
| **Scheduler and GC** | stop-the-world waits on in-flight C calls rather than treating them as safe points |

The distribution row decides it, and it has a documented failure mode: the team
that shipped Rust-to-Go through
[`uniffi-bindgen-go`](https://blogs.agntcy.org/technical/2026/01/27/integrating-rust-and-go-with-uniffi.html)
produced ~150 MB static libraries per platform, over both GitHub's 100 MB file
limit and the module proxy's 500 MB cap, and ended up hosting them on GitHub
Releases behind a separate setup tool. That is a `go get` that no longer just
works — §8's wheel matrix, except Go has no matrix mechanism to fall back on.

So cgo is *acceptable* for a small synchronous pure boundary, which is precisely
the narrow line. It is the artifact story, not the FFI cost, that rules it out
as a default.

### The binding: wasm again, via wazero

[wazero](https://github.com/wazero/wazero) is a WebAssembly runtime written in
pure Go, so `CGO_ENABLED=0` and cross-compilation survive intact and the `.wasm`
can be `go:embed`ed into the module: one artifact for every platform, checked
in, `go get` clean. Arcjet
[ships this exact shape in production](https://blog.arcjet.com/webassembly-on-the-server-compiling-rust-to-wasm-and-executing-it-from-go/)
— Rust core to wasm to Go — chosen over cgo for the cross-compilation reason
above. Three things make the fit better than §7 currently implies:

- **The `wasm32-unknown-unknown` gate is a multi-language asset, not a
  TypeScript one.** §7 sells it as what keeps the browser and edge doors open;
  it is also what makes every future non-Python consumer cheap.
- **The ABI already exists.** §7's measurements "used a raw pointer ABI" — that
  is what Go would consume directly. `wasm-bindgen` is JS-only glue layered on top
  of it.
- **The module can have zero imports** — no WASI, no host functions, and no
  component model, which is wazero's one real gap. `Session::apply` takes no
  randomness at all; only the backoff planner takes an `rng` closure, drawing
  exactly once per call. Have the host pass pre-drawn `f64`s alongside `now`
  instead of importing a callback and the module stays import-free. Worth
  designing for deliberately: it is the difference between a trivial wazero
  target and a host-module one.

The costs are small and real: wazero's optimising compiler covers amd64 and
arm64 with an interpreter fallback roughly 10× slower elsewhere — irrelevant
against §7's headroom — and no mature Rust-to-wasm-to-Go bindgen exists, so the
shim is hand-written. For an envelope-only boundary that is modest, but it is
per-language work.

The alternatives, for the record:

| Option | Verdict |
|---|---|
| [`uniffi-bindgen-go`](https://github.com/NordSecurity/uniffi-bindgen-go) | cgo underneath. Still pinned to uniffi-rs 0.25, 0.x with unclear inter-version stability, manual `Destroy()`, no async story |
| [`purego`](https://github.com/ebitengine/purego) — dlopen/dlsym, no cgo | keeps the Go build cross-compilable but ships a shared library per platform, trading away the static-binary property. Beta, and needs a `fakecgo` shim for glibc's `%fs` conflict |
| A pure Go port of `core-protocol` | The honest fourth option, and §5's own logic half-argues for it: a *pure* state machine can be covered by a recorded corpus far more exhaustively than a driver can. Against it, it is a second brain — the thing §2 exists to eliminate. Credible interim if Go must ship before the binding surface exists |

### What it would change elsewhere

- **§7's asymmetry generalises to one axis.** "Wasm for TypeScript, PyO3 for
  Python" is really *does the host ecosystem already have a way to ship compiled
  artifacts?* Python has wheels, so wasm deepens the matrix — `wasmtime-py` is
  itself a per-platform extension. Go has nothing, so wasm plus a pure-Go
  runtime removes it. Same question, opposite answers, and it predicts the next
  language for free.
- **§8 gains a row.** TypeScript pays nothing, Python pays a wheel matrix, Go
  pays nothing under wazero and everything under cgo. That cliff is sharper than
  either current SDK's, which is why the narrow line matters most here.
- **§9 widens.** "Every SDK bug is potentially a Rust bug" across four
  languages, with a fourth engineer population needing to read Rust. The
  local-override escape hatch is the mitigation, and overriding one decision
  behind an interface is idiomatic Go.

**Trigger to revisit:** a committed Go SDK. The question that would decide the
binding is whether it must build with `CGO_ENABLED=0` — and unlike §7's browser
question, the expected answer closes the cgo door about as firmly as a browser
requirement closes NAPI's.
