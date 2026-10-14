"""Callback contracts for runtime messages and events."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from band.platform.event import (
        ContactEvent,
        ParticipantAddedEvent,
        ParticipantRemovedEvent,
    )
    from band.runtime.contact_tools import ContactTools
    from band.runtime.messages import PlatformMessage
    from band.runtime.tools.agent import AgentTools


MessageHandler = Callable[["PlatformMessage", "AgentTools"], Awaitable[None]]
ContactEventCallback = Callable[["ContactEvent", "ContactTools"], Awaitable[None]]
ParticipantAddedCallback = Callable[[str, "ParticipantAddedEvent"], Awaitable[None]]
ParticipantRemovedCallback = Callable[[str, "ParticipantRemovedEvent"], Awaitable[None]]
