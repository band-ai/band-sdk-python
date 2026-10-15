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

Local events have no REST delivery to replay. If a local turn is deferred, its
queue position is retained while due stopped-room probes can discover a missed
PLAY. These probes do not dispatch newer REST work before the local event is
accepted; newer queued events remain behind it.

While working-state reporting is active, its existing activity keep-alive also
recovers a missed STOP: the platform answers a `working: true` report with
`working: false` when the room execution is stopped. The runtime cancels the
originating turn through the same platform-stop path as a refused claim or post,
leaving an unsettled message PROCESSING for later replay. A provider worker
released for approval retains its originating activity scope across later
deliveries; a late heartbeat STOP cancels that provider without changing the
original message's settled acknowledgement. Responses from before PLAY,
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
one cancellation owner, including explicitly bound Claude, Codex and OpenCode
workers. Activity follows that scope until its handler and registered workers
finish, independently of current delivery admission.
Shutdown takes precedence; otherwise the latest accepted native STOP/INTERRUPT
before settlement determines delivery. An observed REST refusal cannot replace
an accepted native disposition. PLAY advances admission without reviving an
aborted scope, and observer callbacks remain fenced by request-time revision.

The supervisor finishes the old turn's authoritative idle report before the next
working report. Cleanup surviving its bounded grace cannot later stop a newer
turn's activity reporter. Durable completion still retries only its ACK, never
handler effects.
