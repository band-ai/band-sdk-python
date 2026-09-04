"""Chat/room input models -- messaging, events, participants, and room lookup.

Single source of truth for schemas: each class's docstring is the tool
description, and each ``Field(description=...)`` is an argument description.
See ``docs/platform-tools.md``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator

from band.core.content import BLANK_CONTENT_ERROR, has_visible_content
from band.core.types import EventMessageType


def require_visible_content(value: str) -> str:
    """Field-validator body shared by every ``content`` field on this page.

    Exported (not private) so ``band.integrations.mcp.engine``'s
    ``SendEventWideInput`` -- a from-scratch ``create_model`` that widens
    ``message_type`` and so cannot subclass ``SendEventInput`` -- can attach
    the same rule via ``__validators__`` instead of re-deriving it.
    """
    if not has_visible_content(value):
        raise ValueError(BLANK_CONTENT_ERROR)
    return value


class SendMessageInput(BaseModel):
    """Send a message to the chat room.

    Use this to respond to users or other agents. Messages require at least one @mention
    in the mentions array. You MUST use this tool to communicate - plain text responses
    won't reach users.
    """

    content: str = Field(..., description="The message content to send")
    mentions: list[str] = Field(
        ...,
        description=(
            "List of participant handles to @mention. At least one required. "
            "For users: @<username> (e.g., '@john'). "
            "For agents: @<username>/<agent-name> (e.g., '@john/weather-agent')."
        ),
    )

    _validate_content = field_validator("content")(require_visible_content)


class SendEventInput(BaseModel):
    """Send an event to the chat room. No mentions required.

    message_type options:
    - 'thought': Share your reasoning or plan BEFORE taking actions.
      Explain what you're about to do and why.
    - 'error': Report an error or problem that occurred.
    - 'task': Report task progress or completion status.

    Always send a thought before complex actions to keep users informed.
    """

    content: str = Field(..., description="Human-readable event content")
    message_type: EventMessageType = Field(..., description="Type of event")
    metadata: dict[str, Any] | None = Field(
        None, description="Optional structured data for the event"
    )

    _validate_content = field_validator("content")(require_visible_content)


class AddParticipantInput(BaseModel):
    """Add a participant (agent or user) to the chat room.

    IMPORTANT: Use band_lookup_peers() first to find available agents.
    """

    identifier: str = Field(
        ...,
        alias="identifier",
        validation_alias=AliasChoices("identifier", "name"),
        description=(
            "Identifier of participant to add — can be a handle, name, or ID "
            "(from band_lookup_peers). Prefer the exact ID returned by "
            "band_lookup_peers; handles are mainly for mentions."
        ),
    )
    role: Literal["owner", "admin", "member"] = Field(
        "member", description="Role for the participant in this room"
    )


class RemoveParticipantInput(BaseModel):
    """Remove a participant from the chat room."""

    identifier: str = Field(
        ...,
        alias="identifier",
        validation_alias=AliasChoices("identifier", "name"),
        description=(
            "Identifier of the participant to remove — can be a handle, name, or ID"
        ),
    )


class LookupPeersInput(BaseModel):
    """List available peers (agents and users) that can be added to this room.

    Automatically excludes peers already in the room.
    Returns dict with 'data' list of peers and 'metadata' (page, page_size, total_count, total_pages).
    Use this to find specialized agents (e.g., Weather Agent) when you cannot answer
    a question directly.
    """

    page: int = Field(1, ge=1, description="Page number")
    page_size: int = Field(50, ge=1, le=100, description="Items per page (max 100)")


class GetParticipantsInput(BaseModel):
    """Get a list of all participants in the current chat room."""

    pass  # No parameters required


class CreateChatroomInput(BaseModel):
    """Create a new chat room for a specific task or conversation."""

    task_id: str | None = Field(
        default=None, description="Associated task ID (optional)"
    )
