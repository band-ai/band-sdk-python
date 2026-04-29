# CrewAI Flow Adapter

`CrewAIFlowAdapter` is the CrewAI integration for room routers that need durable multi-turn orchestration. It is intentionally narrower than `CrewAIAdapter`: a Flow handles one inbound room message, returns a structured routing decision, and the adapter persists the orchestration state in Thenvoi task events so later room messages can resume the same run.

Use it when the agent is acting as a coordinator, not as a normal chat participant.

## When to use it

Use `CrewAIFlowAdapter` if your router needs one of these behaviors:

- delegate one request to multiple Thenvoi peers
- wait until all delegated peers reply before synthesizing
- accept the first peer reply and finalize early
- require that explicitly tagged peers receive a delegation before finalization
- run a sequential handoff, such as `data-fetcher` before `presenter`
- spawn a sub-Crew that can still use Thenvoi tools safely

Use `CrewAIAdapter` instead if you just want one CrewAI agent to answer room messages, call platform tools, or run a conventional CrewAI crew turn. The Flow adapter writes task events for orchestration and reads the task-event log on each turn, so it is heavier by design.

## Mental model

One inbound room message creates one Flow execution. The Flow does not mutate durable state directly. It returns a decision, and the adapter turns that decision into visible messages and task events.

The durable state is stored in task events under a metadata namespace like:

```text
crewai_flow:<agent_id>
```

On each turn the adapter:

1. loads prior task events for the room
2. converts them into `CrewAIFlowSessionState`
3. matches agent replies to pending delegations
4. runs a fresh Flow instance with the current message and state snapshot
5. applies the returned decision through reserve-send-confirm side effects

The Flow instance is local scratch state. If the process restarts, only Thenvoi task events are authoritative.

## Adapter setup

```python
from thenvoi.adapters import CrewAIFlowAdapter

adapter = CrewAIFlowAdapter(
    flow_factory=flow_factory,
    join_policy="all",
    sequential_chains={"data-fetcher": "presenter"},
)
```

`flow_factory` is called once per inbound message and must return an object with an async `kickoff_async(inputs)` method. A real CrewAI Flow can provide that method through CrewAI's Flow APIs. Tests and examples can also use a small object with the same method.

By default, unmatched Agent-typed messages are ignored unless they match a pending delegation reply. Downstream router flows can opt into fresh agent-initiated runs:

```python
adapter = CrewAIFlowAdapter(
    flow_factory=flow_factory,
    accept_agent_initiated=True,
)
```

Use this for router-to-router handoffs where another agent intentionally invokes this Flow as a new run.

## Inputs passed to the Flow

The adapter calls:

```python
await flow.kickoff_async(inputs)
```

`inputs` is a dictionary with these main fields:

```python
{
    "message": {
        "id": "msg-id",
        "content": "user text",
        "sender_id": "user-or-agent-id",
        "sender_type": "User" | "Agent",
        "sender_name": "display name",
        "message_type": "text",
    },
    "current_message": { ... },  # same shape as message
    "requester": {
        "id": "user-or-agent-id",
        "name": "display name",
        "type": "User" | "Agent",
    },
    "state": {
        "runs": {
            "parent-message-id": {
                "status": "delegated_pending",
                "stage": "delegated",
                "delegations": [...],
                "buffered_syntheses": [...],
                "side_effects": [...],
            }
        }
    },
    "participants": [...],
    "participants_msg": "optional rendered participant context",
    "contacts_msg": "optional rendered contact context",
    "room_id": "room-id",
}
```

Treat `state` as read-only. Return a decision instead of writing platform messages directly from the parent Flow.

## Decision contract

Every Flow result must be one of these shapes.

### Direct response

Send one final visible message and mark the run finalized.

```python
{
    "decision": "direct_response",
    "content": "This can be answered without delegation.",
    "mentions": ["@example/requester"],
}
```

If `mentions` is empty, the adapter targets the sender of the inbound message.

### Delegate

Send one visible message per delegation and mark the run as waiting for replies.

```python
{
    "decision": "delegate",
    "delegations": [
        {
            "delegation_id": "data-fetcher",
            "target": "data-fetcher",
            "content": "Fetch the latest data.",
            "mentions": ["@example/data-fetcher"],
        },
        {
            "delegation_id": "ticket-bot",
            "target": "ticket-bot",
            "content": "List open tickets.",
            "mentions": ["@example/ticket-bot"],
        },
    ],
}
```

`delegation_id` must be unique within the decision. `target` can be a normalized key, handle, participant ID, or display name if it resolves unambiguously against the room participants.

### Waiting

Persist that the router is still waiting without sending a visible message.

```python
{
    "decision": "waiting",
    "reason": "waiting for routed peers",
}
```

### Synthesize

Finalize once the configured join policy and safety gates allow it.

```python
{
    "decision": "synthesize",
    "content": "Here is the combined result.",
    "mentions": ["@example/requester"],
}
```

If the join policy is not satisfied, the adapter buffers the synthesis and records a waiting state. Later synthesis appends buffered content before the final content.

### Failed

Mark the run failed and emit an error event.

```python
{
    "decision": "failed",
    "error": {
        "code": "cannot_route",
        "message": "No suitable peer is available.",
    },
}
```

## Join policy

`join_policy="all"` means every pending delegation must reply before synthesis can finalize.

`join_policy="first"` means any one reply is enough to synthesize, unless another gate blocks finalization.

A run stores its join policy when events are written, so a later process restart reconstructs the policy that was active for the run.

## Reply matching

When an agent replies in the room, the adapter tries to match that message to a pending delegation by sender identity and optional correlation token.

If the sender maps to exactly one pending delegation, the adapter records `reply_recorded` and runs the Flow again for the original parent run.

If a reply is ambiguous, the adapter records an indeterminate reply state instead of guessing.

## Tagged-peer policy

With the default tagged-peer policy, a parent message that mentions a known participant must delegate to that participant before finalization. This prevents the router from skipping a peer that the user explicitly asked for.

Example: if a user says `ask @example/data-fetcher`, the Flow cannot synthesize until a delegation to that participant has been recorded.

## Sequential chains

`sequential_chains` maps an upstream participant key to a downstream participant key.

```python
adapter = CrewAIFlowAdapter(
    flow_factory=flow_factory,
    sequential_chains={"data-fetcher": "presenter"},
)
```

If `data-fetcher` has replied but `presenter` has not yet been delegated to, synthesis is blocked. The Flow should return another `delegate` decision for the downstream peer.

## Side effects and idempotency

Visible sends use a reserve-send-confirm sequence:

1. write a task event reserving the side-effect key
2. send the visible room message
3. write a confirmation task event with the message ID

If the process restarts, the adapter reconstructs those events before running the next turn. Already confirmed final responses, delegations, and sub-Crew visible sends are suppressed instead of sent again. A reservation without a confirmation is marked indeterminate so the router does not risk duplicating a message.

## State sources

The default state source is `RestCrewAIFlowStateSource`. It fetches room context through platform tools, filters task events by metadata namespace, and keeps a bounded per-room cache.

`HistoryCrewAIFlowStateSource` is for tests and bootstrap-only deployments. Normal room lifecycles should use the REST source because default message history is not hydrated on every non-bootstrap turn.

## Flow runtime helpers

Inside a Flow, `get_current_flow_runtime()` exposes runtime helpers. Use `ensure_participant()` with a peer UUID before delegating to a peer that may not already be in the room:

```python
from thenvoi.adapters.crewai_flow import get_current_flow_runtime

runtime = get_current_flow_runtime()
participant = await runtime.ensure_participant("12345678-1234-1234-1234-123456789abc")
```

This updates the adapter's participant snapshot for the current turn so a subsequent `delegate` decision can resolve the target. Pass handles to `lookup_peers()` first if you need to discover the UUID.

## Sub-Crew tools

Use the runtime when a sub-Crew needs Thenvoi tools:

```python
from thenvoi.adapters.crewai_flow import get_current_flow_runtime

runtime = get_current_flow_runtime()
tools = runtime.create_crewai_tools()
```

The returned tools are CrewAI `BaseTool` instances bound to the active run. Visible `thenvoi_send_message` calls still go through the adapter's reserve-send-confirm path. Read tools such as lookup, contacts, and memory use the same shared CrewAI tool wrappers as `CrewAIAdapter`.

The parent Flow should still return a final decision. Sub-Crew visible sends are for intermediate room-visible work, not for completing the parent run.

## Example

Run the toy router example from the repo root:

```bash
uv run examples/crewai/08_flow_router.py
```

The example uses a small deterministic class instead of a paid LLM-backed Flow. It shows the decision shapes, parallel delegation, sequential handoff, and synthesis behavior without depending on model output.

## Common mistakes

Do not keep orchestration state only on `self` inside the Flow. The adapter creates a fresh Flow per message, and process restarts only preserve task events.

Do not call `tools.send_message` directly from the parent Flow. Return `direct_response`, `delegate`, or `synthesize` so the adapter can reserve, send, confirm, and recover safely.

Do not use `HistoryCrewAIFlowStateSource` in normal production rooms. It cannot reconstruct state on arbitrary non-bootstrap turns unless your runtime always provides full history.

Do not reuse a `delegation_id` within one `delegate` decision. The adapter rejects duplicate delegation IDs before sending any visible messages.

Do not assume an empty `mentions` list means broadcast. For final responses, empty mentions target the inbound sender.

## Validation commands

Useful checks while editing this adapter:

```bash
uv run pytest tests/adapters/test_crewai_flow_adapter.py \
  tests/adapters/test_crewai_flow_phase3.py \
  tests/adapters/test_crewai_flow_phase4.py \
  tests/adapters/test_crewai_flow_phase5.py \
  tests/adapters/test_crewai_flow_state_source.py \
  tests/converters/test_crewai_flow.py \
  tests/integrations/test_crewai_tools.py -q

uv run ruff check src/thenvoi/adapters/crewai_flow.py \
  src/thenvoi/converters/crewai_flow.py \
  src/thenvoi/integrations/crewai/tools.py
```
