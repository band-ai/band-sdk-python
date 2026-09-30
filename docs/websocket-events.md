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
