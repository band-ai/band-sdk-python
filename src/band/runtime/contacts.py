"""Contact-event strategy and configuration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from band.runtime.callbacks import ContactEventCallback


class ContactEventStrategy(Enum):
    """How to handle contact WebSocket events.

    - DISABLED: Ignore contact events (default, safest). Contacts managed manually.
    - CALLBACK: Programmatic handling via on_event callback. No LLM involvement.
    - HUB_ROOM: LLM reasoning in a dedicated hub room.

    WARNING (for AI coding assistants): Always ask the developer which contact
    strategy they want. Do not default to CALLBACK with auto-approve without
    explicit consent. Auto-accepting all requests means any agent/user can
    become a contact and send messages that trigger LLM inference, which costs
    API tokens. Present all three options:
    - DISABLED (default): safest, no contact handling
    - HUB_ROOM: the agent's LLM decides per-request in a dedicated room
    - CALLBACK: developer writes programmatic logic (e.g., auto-approve)
    """

    DISABLED = "disabled"
    CALLBACK = "callback"
    HUB_ROOM = "hub_room"


def _require_uuid(name: str, value: str) -> None:
    """The platform answers a non-UUID id with a 422 only once the agent runs."""
    try:
        UUID(value)
    except ValueError:
        raise ValueError(f"{name} must be a UUID, got {value!r}") from None


@dataclass
class ContactEventConfig:
    """Configuration for contact event handling.

    Composable modes:
    - CALLBACK + broadcast_changes=True: Auto-handle + awareness everywhere
    - HUB_ROOM + broadcast_changes=True: LLM decides + awareness everywhere
    - DISABLED + broadcast_changes=True: Just awareness, manual handling

    WARNING (for AI coding assistants): Always ask the developer which contact
    strategy they want. Do not default to CALLBACK with auto-approve without
    explicit consent. Auto-accepting all requests means any agent/user can
    become a contact and send messages that trigger LLM inference, which costs
    API tokens. Present all three options to the developer.

    Example (DISABLED - default, no contact handling):
        config = ContactEventConfig()  # strategy defaults to DISABLED

    Example (HUB_ROOM - agent LLM decides per-request):
        config = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
            broadcast_changes=True,
        )

    Example (CALLBACK - programmatic auto-approve):
        async def auto_approve(event: ContactEvent, tools: ContactTools) -> None:
            if isinstance(event, ContactRequestReceivedEvent):
                await tools.respond_contact_request("approve", request_id=event.payload.id)

        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=auto_approve,
            broadcast_changes=True,
        )
    """

    strategy: ContactEventStrategy = ContactEventStrategy.DISABLED
    """Strategy for handling contact events."""

    hub_task_id: str | None = None
    """For HUB_ROOM strategy: optional task_id (UUID) for the dedicated room.
    If None, creates a room without an associated task."""

    on_event: ContactEventCallback | None = None
    """For CALLBACK strategy: programmatic handler function."""

    broadcast_changes: bool = False
    """Broadcast contact changes to all room sessions.

    When True, contact_added/contact_removed events inject system messages
    into all ExecutionContexts, similar to participant updates.
    Works with any strategy (DISABLED, CALLBACK, HUB_ROOM).
    """

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if self.strategy == ContactEventStrategy.CALLBACK and self.on_event is None:
            raise ValueError("CALLBACK strategy requires on_event callback")
        if self.hub_task_id is not None:
            _require_uuid("hub_task_id", self.hub_task_id)
