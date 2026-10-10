# WebSocket Channels & Events

Channels, events and payload fields are defined in `src/band/client/streaming/client.py`
and `src/band/platform/event.py`. What the code cannot say for itself:

- **Field rules live in `band-sdk-core`**, not in the SDK payload models. The models are
  rule-free typed projections: `WirePayload.from_wire` has core validate and normalize the
  payload (alias sync, defaulting, coercion), then hydrates the model without
  re-validating.
- **Delivery-lifecycle decisions are core's too.** `OneShotInvoker`
  (`src/band/runtime/oneshot.py`) is a thin caller-owns-the-loop wrapper over core's
  `evaluate_delivery_event`, `evaluate_next_message`, `evaluate_drain_candidate` and
  `evaluate_adapter_result`: event routing, drain-candidate classification (including
  self-echo) and the ack decision are core's, not SDK logic.
- **`ExecutionContext` is a separate machine.** It calls none of those functions. It dedups
  on `metadata.delivery_status` and calls core's `is_self_echo` directly.

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
at the base interval. A current nonempty response permits discovery of a missed
PLAY, followed by normal acknowledgement recovery and an accepted claim. Newer
STOP, PLAY or INTERRUPT controls invalidate obsolete recovery results. A 204
response preserves the local pause because it cannot distinguish stopped from
empty rooms.

Failed requests, refused claims and pending acknowledgements retain the base
retry cadence. A completed or skipped head that cannot advance ends the pass
without spinning; retryable handler failures retain their normal attempt budget.
These intervals bound scheduling, subject to active handlers, HTTP operations
and recovery retries.
