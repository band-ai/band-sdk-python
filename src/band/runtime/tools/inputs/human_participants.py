"""Human-tool input models: chat room participants.

See ``human_agents`` for the field-for-field-mirrors-band-mcp note that
applies to every human-tool input model.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ListMyChatParticipantsInput(BaseModel):
    """List participants in a chat room."""

    chat_id: str = Field(..., description="The chat room ID (required).")
    participant_type: str | None = Field(
        None, description="Filter by type: 'User' or 'Agent' (optional)."
    )


class AddMyChatParticipantInput(BaseModel):
    """Add a participant to a chat room."""

    chat_id: str = Field(..., description="The chat room ID (required).")
    participant_id: str = Field(
        ..., description="ID of user or agent to add (required)."
    )
    role: str | None = Field(
        None,
        description="'owner', 'admin', or 'member' (optional, defaults to 'member').",
    )


class RemoveMyChatParticipantInput(BaseModel):
    """Remove a participant from a chat room."""

    chat_id: str = Field(..., description="The chat room ID (required).")
    participant_id: str = Field(
        ..., description="ID of participant to remove (required)."
    )
