# WebSocket Channels & Events

Channels, events and payload fields are defined in `src/band/client/streaming/client.py`
and `src/band/platform/event.py`. What the code cannot say for itself:

- **Field rules live in `band-sdk-core`**, not in the SDK payload models. The models are
  rule-free typed projections: `WirePayload.from_wire` has core validate and normalize the
  payload (alias sync, defaulting, coercion), then hydrates the model without
  re-validating.
- **Terminal delivery rules live in core.** `OneShotInvoker`
  (`src/band/runtime/oneshot.py`) uses `evaluate_delivery_event`,
  `evaluate_drain_candidate` and `evaluate_adapter_result` for event routing,
  history drain classification and terminal acknowledgement. Python owns
  ordinary-work ordering and adapter-control reachability. Definitively rejected
  input returns before the terminal acknowledgement evaluator.
- **`ExecutionContext` owns room scheduling.** It reuses core's claims, retry
  tracking, participant roster and self-echo rules, with delivery metadata for
  deduplication.
- **Core owns the claim and settlement state.** `ClaimRegistry` holds in-flight
  IDs, the completed-ID cache and each original's pending terminal intent
  (`processed`, or `failed` with its error, plus a parked flag); see
  [turn outcome](turn-outcome.md#terminal-intents-and-settlement). Python owns
  which originals form a turn and in what order. The no-reply tool text is core's
  too. The Python `SettlementResult` classification of a terminal write is SDK
  code ([REST client](rest-client.md#terminal-write-results)).

## Room reconciliation

`ExecutionContext` checks a persistent monotonic deadline before dequeuing events.
Unrelated traffic cannot postpone recovery of a missed push. Startup, PLAY and
reconnect request reconciliation at the next serial boundary; handlers are never
preempted by the periodic deadline.

`SessionConfig.idle_resync_seconds` sets the base interval (60 seconds). Confirmed
empty HTTP 204 responses without new message work double the interval up to
`idle_resync_max_seconds` (120 seconds). Only the first periodic check is randomly
spread within the base interval. New message work restores the base interval.
Both values must be finite and positive; the effective cap is `max(base, maximum)`,
so a maximum at or below the base disables growth.

A received STOP suppresses local message triggers while `/next` probes continue
at a fixed interval: `SessionConfig.stopped_resync_seconds`, or the base interval
when unset. Stopped probes never grow, and a resumed room returns to the base
interval. A longer stopped interval lowers request volume for paused rooms but
delays discovering a missed PLAY by up to that long. `STOPPED_RESYNC_SECONDS`
provides an environment default through Pydantic settings; an explicit
`SessionConfig(stopped_resync_seconds=...)` takes precedence, including `None`
to use the base interval. Set values must be finite and positive; an empty
environment value is ignored. A current nonempty response permits discovery of
a missed PLAY, followed by normal acknowledgement recovery and an accepted claim.
Newer STOP, PLAY or INTERRUPT controls invalidate obsolete recovery results. A 204
response preserves the local pause because it cannot distinguish stopped from
empty rooms.

While working-state reporting is active, its existing activity keep-alive also
recovers a missed STOP: the platform answers a `working: true` report with
`working: false` when the room execution is stopped. The runtime cancels the
originating turn through the same platform-stop path as a refused claim or post,
leaving its message PROCESSING for later replay. Responses from before PLAY,
a replacement turn, or shutdown cannot stop newer work. Request failures never
count as STOP, and the public activity-report boolean still means request success.

This recovery ends when working-state reporting is disabled, capped, or excluded
for a hub room. It does not recover INTERRUPT or a STOP cleared before the next
report. Native control pushes remain the immediate cancellation path.

Failed requests, refused claims and pending acknowledgements retain the base
retry cadence. A completed or skipped head that cannot advance ends the pass
without spinning; retryable handler failures retain their normal attempt budget.
These intervals bound scheduling, subject to active handlers, HTTP operations
and recovery retries.

`SessionConfig` uses Pydantic settings for all fields. Environment names match
the field names without regard to case, explicit keyword arguments override
the environment, and empty environment values use defaults. For example,
`IDLE_RESYNC_SECONDS=30` sets the base interval. Construct it with keyword
arguments and serialize it with `model_dump()`; dataclass utilities and
positional construction are no longer supported.

## Pending work and control recovery

A provider attempt that raises `InputNotAccepted` leaves its delivery
outstanding. The next scheduler pass retries the oldest work once at the base
reconciliation cadence. It releases local ownership and returns that attempt's
failure-budget charge; adapters do not wait or retry internally. Durable work
stays in platform rows, while synthetic messages remain in the local room queue.
No scopes or tools survive merely to retry an input.

`/next` exposes only the oldest outstanding delivery. While that work is blocked,
the runtime also scans one chronological page of actionable `/messages` per
pass to find missed controls, retaining the cursor across ordinary traffic.
A changed pending head or the end of the scan starts a new scan. PLAY and
reconnect bring a pass forward without rewinding its cursor.

Controls use the same duplicate checks and accepted processing gate before an
adapter handles them. A control consumed behind pending work acknowledges only
its own delivery; an ordinary candidate is left outstanding. Runtime recovery
never interprets approval syntax or drains model context for a control.

## Control settlement

Room admission and control revisions belong to the execution context; a turn
scope owns termination intent and delivery disposition. Each concrete task has
one cancellation owner, including explicitly bound Codex/Claude workers.
Shutdown takes precedence; otherwise the latest accepted native STOP/INTERRUPT
before settlement determines delivery. An observed REST refusal cannot replace
an accepted native disposition. PLAY advances admission without reviving an
aborted scope, and observer callbacks remain fenced by request-time revision.

The supervisor finishes the old turn's authoritative idle report before the next
working report. Cleanup surviving its bounded grace cannot later stop a newer
turn's activity reporter. Durable completion still retries only its ACK, never
handler effects.

## Queued-message selection

With `SessionConfig.consume_all_queued` ([guide](consume-all-queued.md)) the room
selects its next turn from two sources and takes a bounded, ordered prefix of
the ordinary messages.

- **REST list.** One page of `GET agent/chats/{room}/messages` per scheduling
  pass: ascending cursor pagination with no status filter, 100 per page, ordered
  by `(inserted_at, id)`. It returns outstanding agent-addressed text, including
  `processing` and `failed` deliveries.
- **Retained WebSocket originals.** The list and `/next` omit some deliveries the
  WebSocket carries, such as thread replies, and the `message_created` payload
  does not reliably carry `thread_id`, so coverage is never classified from it.
  The room therefore keeps each delivered original's payload, not just its ID,
  until the original settles.

The two sources merge by original ID into one chronological list: an original in
both counts once, keeping the retained payload and the REST delivery status. A
retained original the REST read lacks is trusted only once the read has reached
or passed its `(inserted_at, id)` or ended; it then runs as a singleton, after
the eligible earlier prefix. Until then the prefix stops short of it, so a
continuous stream of arrivals never makes a turn wait for the end of the list.

Self-authored, locally settled or settlement-pending deliveries, and those the
platform reports `processed` or `failed` for this agent, are skipped. A delivery
whose claim another owner holds ends the prefix and defers it. The `failed` rule reads the REST metadata's `delivery_status` for this agent and does
not depend on the bounded completed-ID cache, so it holds after a restart too
([how the field reaches the SDK](rest-client.md#delivery-status-metadata)).

### Event order

Non-message events (participant add/remove, synthetic turns) are kept in the same
queue as messages, in the order the room observed them. Participant events carry
no server timestamp comparable to a message's, so that local order is the only
order used.

- The first queued non-message event bounds the current segment: no REST message
  past the segment's last original joins the turn, because its position relative
  to the event is unknown.
- A leading non-message event runs on its own before the following segment.
- The queue is absorbed again after the REST read and before the membership is
  fixed. Events arriving after that point apply at the next serial boundary and
  never enlarge the running turn.

### Scan progress

One page is read per pass. A page holding only skipped deliveries with a next
cursor saves the cursor and schedules an immediate continuation after yielding to
the event loop, not after the periodic resync delay. At the end of the list the
cursor resets and the room returns to normal wake or idle scheduling. There is
no skip floor over `failed` rows: another runtime can reopen one as `processing`
and crash, and a later scan must find it. The cost is that failed history ahead of
new work is re-read page by page. True ownership, admission or write blockage
stays on the paced retry cadence.

### Limits of WebSocket-only backlog

A retained original survives local deferral and STOP because the room keeps its
payload. It does not survive the loss of that payload (process exit, or retirement
of the room's execution): neither the list nor `/next` can rebuild a thread reply
afterwards.

The stopped-room `/next` probe is unchanged and still gates PLAY discovery, but
for backlog that is WebSocket-only its HTTP 204 cannot tell a room that resumed
with nothing to do from one still stopped whose PLAY push was missed. The SDK adds
no durable journal or extra polling route to remove that limit.
