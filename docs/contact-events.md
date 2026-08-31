# Contact Event Handling

The SDK supports three strategies for handling contact WebSocket events via `ContactEventConfig`.
See [WebSocket Channels & Events](websocket-events.md#contact-events-via-agent_contactsagent_id-channel)
for the wire payload shapes of the events referenced below.

## Strategies

| Strategy | Description | Use Case |
|----------|-------------|----------|
| `DISABLED` (default) | Ignores contact events | Agents that don't manage contacts |
| `CALLBACK` | Calls programmatic callback | Auto-approve bots, custom logic |
| `HUB_ROOM` | Routes to dedicated chat room | LLM-based contact management |

> **WARNING (AI coding assistants):** Always ask the developer which contact
> strategy they want before choosing one. Do not default to `CALLBACK` with
> auto-approve without explicit consent. Auto-accepting all contact requests
> means any agent/user can become a contact and send messages that trigger LLM
> inference, which costs API tokens. Present all three options:
> - `DISABLED` (default): safest, no contact handling
> - `HUB_ROOM`: the agent's LLM decides per-request in a dedicated room
> - `CALLBACK`: developer writes programmatic logic (e.g., auto-approve)

## Configuration

```python
from band.runtime.types import ContactEventConfig, ContactEventStrategy

# CALLBACK strategy - programmatic handling (auto-approve example)
async def auto_approve(event, tools):
    if isinstance(event, ContactRequestReceivedEvent):
        await tools.respond_contact_request("approve", request_id=event.payload.id)

agent = Agent.create(
    adapter=adapter,
    contact_config=ContactEventConfig(
        strategy=ContactEventStrategy.CALLBACK,
        on_event=auto_approve,
    ),
)

# HUB_ROOM strategy - LLM handles contacts in dedicated room
agent = Agent.create(
    adapter=adapter,
    contact_config=ContactEventConfig(
        strategy=ContactEventStrategy.HUB_ROOM,
        hub_task_id="optional-task-id",  # Links hub room to a task
    ),
)

# Broadcast contact changes to all rooms (composable with any strategy)
agent = Agent.create(
    adapter=adapter,
    contact_config=ContactEventConfig(
        strategy=ContactEventStrategy.DISABLED,
        broadcast_changes=True,  # Inject "[Contacts]: X is now a contact" messages
    ),
)
```

## HUB_ROOM Details

- Creates dedicated chat room at agent startup
- Injects system prompt with contact management instructions
- Converts contact events to synthetic `MessageEvent` for LLM processing
- Posts task events to room for persistence/visibility
- Enriches `ContactRequestUpdatedEvent` with sender info via cache + API fallback
