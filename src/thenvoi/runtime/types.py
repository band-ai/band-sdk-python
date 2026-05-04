"""
Runtime types for Thenvoi agent SDK.

Extracted from core/types.py - data structures used across the runtime layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable

# --- Constants for synthetic messages (injected by SDK, not from platform) ---
#
# These constants define the sender identity for SDK-generated messages that are
# injected into the event queue but don't originate from the platform WebSocket.
#
# Primary use case: ContactEventHandler's HUB_ROOM strategy creates synthetic
# MessageEvents to route contact events to the hub room for LLM processing.
# These messages need a consistent sender identity that:
#   1. Clearly indicates they're system-generated (not from a real user/agent)
#   2. Can be filtered by preprocessing if needed
#   3. Are recognizable in UI/logs for debugging
#

# Sender type for synthetic messages. Matches the platform's "System" type used
# for other system-generated content.
SYNTHETIC_SENDER_TYPE = "System"

# Sender ID for synthetic contact event messages. This is a logical identifier
# (not a UUID) that allows filtering/identification of contact-related synthetic
# messages. Used in MessageEvent.payload.sender_id for hub room injections.
SYNTHETIC_CONTACT_EVENTS_SENDER_ID = "contact-events"

# Human-readable sender name displayed in UI/logs for synthetic contact event
# messages. Used in MessageEvent.payload.sender_name.
SYNTHETIC_CONTACT_EVENTS_SENDER_NAME = "Contact Events"


def normalize_handle(handle: str | None) -> str | None:
    """
    Normalize a handle to always include the @ prefix.

    Handles may or may not include the @ prefix depending on the source.
    This function ensures consistent formatting.

    Args:
        handle: The handle to normalize (may or may not have @ prefix)

    Returns:
        Handle with @ prefix, or None if input is None/empty
    """
    if not handle:
        return None
    return handle if handle.startswith("@") else f"@{handle}"


if TYPE_CHECKING:
    from thenvoi.platform.event import (
        ContactEvent,
        ParticipantAddedEvent,
        ParticipantRemovedEvent,
    )

    from .contact_tools import ContactTools
    from .tools import AgentTools


@dataclass
class AgentConfig:
    """Configuration for agent runtime."""

    auto_subscribe_existing_rooms: bool = True


@dataclass
class SessionConfig:
    """Configuration for execution context."""

    enable_context_cache: bool = True
    context_cache_ttl_seconds: int = 300
    max_context_messages: int = 100
    max_message_retries: int = 1  # Max attempts per message before permanently failing
    enable_context_hydration: bool = True  # Whether to fetch history from platform API
    # Phase 2 idle timeout (seconds) before re-polling /next as a safety net.
    # Lower values recover faster from missed WS pushes but generate more REST traffic.
    # With N rooms, each resync fires N parallel /next polls. Default 60s balances
    # recovery speed against REST load for typical single-agent deployments.
    # Uses float so tests can exercise sub-second values without forcing prod to
    # round. Must be > 0; zero or negative turns Phase 2 into a REST hot loop.
    idle_resync_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.idle_resync_seconds <= 0:
            raise ValueError(
                "idle_resync_seconds must be > 0 (got %s)" % self.idle_resync_seconds
            )


@dataclass
class PlatformMessage:
    """
    Message from platform (normalized for adapters).

    This is the message format passed to MessageHandlers.
    """

    id: str
    room_id: str
    content: str
    sender_id: str
    sender_type: str  # "User", "Agent", "System"
    sender_name: str | None
    message_type: str
    metadata: dict[str, Any]
    created_at: datetime

    def format_for_llm(self) -> str:
        """
        Format message with sender prefix for LLM consumption.

        Returns string in format: [SENDER_NAME]: message content
        """
        sender = self.sender_name or self.sender_type
        return f"[{sender}]: {self.content}"


@dataclass
class ConversationContext:
    """
    Hydrated context for a room.

    Contains conversation history and participant information
    for context-aware processing.
    """

    room_id: str
    messages: list[dict[str, Any]]
    participants: list[dict[str, Any]]
    hydrated_at: datetime


# Callback type - receives AgentTools, NOT ThenvoiAgent
MessageHandler = Callable[["PlatformMessage", "AgentTools"], Awaitable[None]]


# --- Contact Event Configuration ---


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


# Type alias for contact event callback
ContactEventCallback = Callable[["ContactEvent", "ContactTools"], Awaitable[None]]
ParticipantAddedCallback = Callable[[str, "ParticipantAddedEvent"], Awaitable[None]]
ParticipantRemovedCallback = Callable[[str, "ParticipantRemovedEvent"], Awaitable[None]]


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
