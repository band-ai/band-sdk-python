# WebSocket Channels & Events

Reference for the Phoenix Channels the SDK subscribes to and the typed
payload shapes each channel's events carry.

## Channels (Phoenix Channels Protocol V2)

| Channel | Topic Format | Events |
|---------|--------------|--------|
| Agent Rooms | `agent_rooms:{agent_id}` | `room_added`, `room_removed` |
| Chat Room | `chat_room:{chat_room_id}` | `message_created` |
| User Rooms | `user_rooms:{user_id}` | `room_added`, `room_removed` |
| Room Participants | `room_participants:{chat_room_id}` | `participant_added`, `participant_removed` |
| Tasks | `tasks:{user_id}` | `task_created`, `task_updated` |

## Payload Models (Pydantic)

Field rules and normalization (alias sync, defaulting, coercion) live in
`band-sdk-core` (`band_sdk_core.validate_event_payload`), not in these
models — they are rule-free typed projections, hydrated without
re-validating by `WirePayload.from_wire` (`src/band/client/streaming/wire.py`).
Every model inherits `WirePayload`, which sets `ConfigDict(extra="allow")`
once for all of them.

`band-sdk-core` is also where the one-shot delivery-lifecycle *decisions*
live — `evaluate_delivery_event`/`evaluate_next_message`/
`evaluate_drain_candidate`/`evaluate_adapter_result` — not just payload
validation. `OneShotInvoker` (`src/band/runtime/oneshot.py`) is a thin
caller-owns-the-loop wrapper around those four functions: the
ignore/cleanup/self-echo/invocation routing, the drain-candidate
classification, and the ack decision are core's, not the SDK's own logic.
`ExecutionContext` is a different machine with its own dedup model
(`metadata.delivery_status`) and does not call `evaluate_delivery_event`/
`evaluate_next_message`/`evaluate_drain_candidate` — it shares only the
`is_self_echo` predicate with `OneShotInvoker`.

```python notest
MessageCreatedPayload:
  id, content, message_type, sender_id, sender_type,
  sender_name?, metadata? (MessageMetadata), chat_room_id?,
  thread_id?, inserted_at, updated_at

MessageMetadata:
  mentions (list[Mention]), status?

RoomAddedPayload:
  id, inserted_at, updated_at, title?, task_id?

RoomRemovedPayload:
  # Same 5-field wire shape as RoomAddedPayload -- band-sdk-core validates
  # both through one rule (ChatJSON.format_room_event/1).
  id, inserted_at, updated_at, title?, task_id?

ParticipantAddedPayload:
  id, name, type, handle?, description?, is_remote?, is_external? (legacy alias)

ParticipantRemovedPayload:
  id, name, type

Mention:
  id, username?, handle?, name?
```

## PlatformEvent Union (Tagged Union Pattern)

```python notest
PlatformEvent = (
    MessageEvent | RoomAddedEvent | RoomRemovedEvent
    | ParticipantAddedEvent | ParticipantRemovedEvent
)
```

Each event has: `type` (literal), `room_id`, `payload`, `raw`

## Contact Events (via `agent_contacts:{agent_id}` channel)

| Event | Payload Fields |
|-------|----------------|
| `contact_request_received` | `id`, `from_handle`, `from_name`, `message?`, `status`, `inserted_at` |
| `contact_request_updated` | `id`, `status` |
| `contact_added` | `id`, `handle`, `name`, `type`, `description?`, `is_remote?`, `is_external?` (legacy alias; mirrors `is_remote`), `inserted_at` |
| `contact_removed` | `id` |

See [Contact Event Handling](contact-events.md) for how the SDK routes these
events to an agent.
