# AgentCore × Thenvoi — how it works

A conceptual overview of the integration. For a step-by-step deploy guide
see [`README.md`](README.md); for writing your own agent see
[`BUILDING.md`](BUILDING.md).

## The problem

Thenvoi (Band) agents are event-driven: each agent holds a persistent
WebSocket subscription to the platform and reacts to messages as they
arrive. The standard SDK flow (`Agent.create(...).run()`) assumes you can
run a long-lived process that owns that WebSocket.

AWS Bedrock AgentCore Runtime is the opposite: a serverless,
request/response host. Each invocation is an HTTP call to a microVM that
serves a single response and then goes idle. The runtime caps sessions at
**15 minutes idle** and **8 hours maximum**, and the container has no
mechanism to listen for inbound events between invocations.

So we can't just `pip install thenvoi-sdk` inside an AgentCore container
and call `Agent.create(...).run()` — the WS would die in 15 minutes and
the runtime would shut it down. We need a different shape.

## The shape

Two cooperating pieces:

```
   THENVOI PLATFORM (Phoenix WS + REST)
        ▲
        │ WS subscription per agent identity
        │ REST calls (read context, send messages)
        ▼
   ┌─────────────────────┐   HTTP POST     ┌──────────────────────────┐
   │  BRIDGE             │  /invocations    │  AGENTCORE CONTAINER     │
   │  (long-running)     │ ──────────────▶ │  (one image, one ARN     │
   │                     │                  │   per agent identity)    │
   │  - holds the WS     │                  │  - holds the Thenvoi     │
   │  - forwards events  │                  │    SDK + Anthropic SDK   │
   │  - no Band logic    │                  │  - one LLM call per      │
   │                     │                  │    invocation            │
   └─────────────────────┘                  └──────────────────────────┘
```

### Bridge (`thenvoi-bridge/`)
- A small Python process that maintains one Thenvoi WS subscription **per
  agent identity** (so a single bridge can host `@weather`, `@math`,
  `@personal_assistant`, etc. concurrently).
- Receives platform events (mentions, room added/removed, participant
  changes), wraps them in a JSON envelope, and forwards them to the
  agent's container endpoint via either:
  - **HTTP POST** to a plain URL, or
  - **`bedrock-agentcore:InvokeAgentRuntime`** via boto3 (SigV4-signed).
- That's it. **No mention parsing, no message construction, no lifecycle
  marking.** The bridge is a dumb pipe.

### Container (`examples/agentcore/agentcore_llm_server.py`)
- A thin FastAPI app implementing the AgentCore Runtime contract:
  - `GET /ping` — health probe.
  - `POST /invocations` — one event in, one response out.
- The container is just transport + env-driven adapter construction. All the
  lifecycle logic lives in the SDK's `OneShotInvoker`
  (`thenvoi.runtime.oneshot`), which the container wraps. On each invocation
  it calls `await invoker.handle_event(forwarded_body)`, and `OneShotInvoker`:
  1. Reconstructs a typed `PlatformMessage` from the forwarded JSON.
  2. Fetches participants and recent room history from Thenvoi REST.
  3. Builds `AgentInput` and calls `adapter.on_event(...)` (default
     adapter: `AnthropicAdapter`).
  4. Adapter runs the LLM tool loop; tools call back to Thenvoi REST
     (`send_message`, `add_participant`, `lookup_peers`, …).
  5. Returns a status dict; the container returns 200 to the bridge.
- Because `OneShotInvoker` is in the SDK, any request/response host (Lambda,
  Cloud Run, …) can reuse it — the container is one ~80-line example of how.

## Why the bridge has no Band logic

The original prototype put mention parsing, lifecycle marking, and
`AgentTools` inside the bridge. We pulled them out for three reasons:

1. **Single source of truth.** The SDK already implements all of those
   things. Duplicating them in the bridge meant two implementations
   could drift.
2. **Trust boundary.** Each container holds its own Thenvoi API key.
   The bridge needs *only* WS subscription credentials, which can be
   scoped more tightly than full agent credentials.
3. **Container freedom.** A team can build an AgentCore agent in any
   framework (LangGraph, CrewAI, pydantic-ai, etc.) as long as their
   container speaks the Thenvoi SDK. The bridge stays out of that
   decision.

The bridge therefore only knows about Phoenix WS protocol and
HTTP/AgentCore transports.

## What happens in one invocation

Real example — user asks `@personal_assistant` "what's the temperature
difference between Tel Aviv and Warsaw, in percent?":

```
1. User posts message in room R, mentioning @PA.
2. Thenvoi WS → bridge (subscribed as PA) receives MessageEvent.
3. Bridge POSTs the event to PA's AgentCore runtime ARN.
4. AgentCore Runtime cold-starts a microVM (or reuses a warm one).
5. PA's container:
   a. mark_processing(triggering_msg_id)
   b. fetch participants + history via Thenvoi REST
   c. AnthropicAdapter runs Claude with the AgentTools schemas
   d. Claude emits tool_use: lookup_peers → add_participant("weather")
      → add_participant("math") → send_message(@weather Tel Aviv?)
      → send_message(@weather Warsaw?)
   e. Each send_message hits Thenvoi REST as a side effect
   f. mark_processed(triggering_msg_id)
   g. drain: any other un-handled mentions get marked processed too
   h. Return 200 to bridge
6. Bridge sees the next room event (PA's mention of @weather), forwards
   to weather's runtime. Same flow.
7. ...eventually PA is re-invoked after both peers reply, synthesizes,
   and posts the final answer to the user.
```

## Concurrency: how we prevent duplicate work

The interesting failure mode: PA emits two `@weather` messages in quick
succession (sub-second apart). Weather's WS subscription gets two
`message_created` events back-to-back. Naively, those two events would
trigger two concurrent invocations of weather's runtime, and each LLM
call would see *both* questions un-answered → both would respond to both
→ four messages instead of two.

We prevent that with two coordinated mechanisms:

### Bridge: per-room serialization
`AgentRunner` holds an `asyncio.Lock` per `room_id`. Events for the same
room serialize through the forwarder; different rooms still forward in
parallel. So when PA's two messages hit weather's WS, the bridge calls
`InvokeAgentRuntime` **sequentially** — weather invocation B doesn't
start until invocation A's forward call returns.

### Container: lifecycle markers + drain
Inside `OneShotInvoker.handle_event`, each invocation:

1. Calls `link.get_next_message(room_id)` — the platform returns the
   next unprocessed message for this agent. If it's not the triggering
   message (or no message is open), skip the LLM call.
2. Calls `link.mark_processing(room_id, msg_id)` to claim the message.
3. Runs the LLM (which sees the *full* history including any other
   un-answered mentions), recording the message ids in that history
   snapshot as `seen_ids`.
4. On success, calls `mark_processed(room_id, msg_id)`.
5. **Drains**: while `get_next_message` returns a message *that was in
   `seen_ids`*, call `mark_processing` + `mark_processed` on it. The LLM
   had visibility into those during its turn — whatever was unanswered is
   now this agent's responsibility, replied or not. A message that arrived
   *after* the snapshot is **not** swallowed: drain stops and leaves it
   open so the next invocation handles it with fresh context. (Self-
   messages are skipped defensively; hitting the drain cap surfaces
   `drain_truncated: true` in the response.)

When weather invocation B starts (after A finishes), B's
`get_next_message` returns `204 No Content` — A drained it. B exits with
`{"status": "no_pending"}` without an LLM call.

This is the same in-band claim/process semantics the SDK's
`ExecutionContext` uses in the normal long-running Agent flow;
`OneShotInvoker` reshapes it for the request/response model.

## Startup rehydration: catching up after downtime

Phoenix only pushes events from subscription time forward. So a message
that arrives while the bridge is down — or one left stuck in `processing`
when a container crashed mid-turn — is never redelivered on the WS once
the bridge reconnects. Without a catch-up step, the agent would stay
silent on that backlog until some *new* event happened to land in the
room.

On every (re)connect, after subscribing to its existing rooms, each
`AgentRunner` polls `link.get_next_message(room_id)` (the platform's
`/next`) once per room. If a room has an unprocessed message, the bridge
forwards the oldest one as a synthetic `message_created` event through the
same dedup + per-room-lock + forwarder path a live event takes — so the
container can't tell a rehydrated nudge from a live message.

Two things make one nudge per room enough:

- The container's **drain loop** pulls the rest of that room's backlog
  once it processes the first message, so the bridge needn't replay all of
  them.
- `/next` also returns messages stuck in `processing`, so this doubles as
  **cross-restart crash recovery**: the forwarded `msg_id` matches the
  container's own `/next`, so the container *reclaims* the stuck message
  (re-`mark_processing` resets the attempt) rather than skipping it as
  already-claimed.

This stays within the dumb-pipe boundary: rehydration is a *read*
(`/next`) plus a forward. The bridge still parses no mentions, constructs
no messages, and marks no lifecycle state — the container owns all of
that. (One genuinely uncovered case remains: a message stuck in
`processing` while the bridge stays connected, with no new traffic and no
reconnect — nothing re-polls `/next`. Covering it would need a periodic
sweep, deliberately out of scope for now.)

## Constraints to know

| Constraint | Implication |
|---|---|
| AgentCore session **8-hour max**, **15-min idle timeout** | Each Band room maps to one `runtimeSessionId`; very long-running orchestrations need to be designed for graceful restart. |
| **Per-room** `runtimeSessionId` (derived from `room_id`) | Different rooms get different microVMs; concurrent rooms parallelize naturally. |
| **Cold start** on first invocation per session | Adds ~1-2s to the first event in a new room. |
| Container env vars are **plaintext** in the runtime config | For production, use AgentCore Identity / Secrets Manager rather than embedding `THENVOI_API_KEY` directly. |
| `mark_processed` requires prior `mark_processing` | The drain loop pairs them explicitly. |

## When NOT to use this pattern

- **Always-on agents that need to monitor a room's full history in real
  time** (e.g. a moderation bot watching every message). Use the SDK's
  normal `Agent.create(...).run()` on ECS / Fargate / a VM — anywhere
  that can hold a persistent WS.
- **Sub-second response latency requirements.** AgentCore cold starts +
  AgentCore Runtime overhead add seconds; a co-located ECS agent is
  faster.
- **Cost-sensitive workloads with very high message rates.** Per-event
  AgentCore invocations are pay-per-use; if your agent is invoked
  thousands of times per hour, ECS/Fargate may be cheaper.

For batch-style agents that respond to mentions, this integration is the
right fit.

## Follow-ups we'd still like to do

These are not required for the demo to work, but worth knowing:

- **AgentCore Gateway as Band tool broker**: register Thenvoi REST as
  Gateway targets so multiple agents share one tool surface.
- **A2A protocol bridge**: alternate inbound path using AgentCore's
  native A2A support.

See [INT-506](https://linear.app/thenvoi/issue/INT-506) for the design
discussion that produced this architecture.
