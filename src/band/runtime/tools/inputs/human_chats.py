"""Human-tool input models: chat room listing and creation.

See ``human_agents`` for the field-for-field-mirrors-band-mcp note that
applies to every human-tool input model.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ListMyChatsInput(BaseModel):
    """List chat rooms where the user is a participant."""

    page: int | None = Field(None, description="Page number (optional).")
    page_size: int | None = Field(None, description="Items per page (optional).")


class GetMyChatRoomInput(BaseModel):
    """Get a specific chat room by ID."""

    chat_id: str = Field(..., description="The chat room ID (required).")


class CreateMyChatRoomInput(BaseModel):
    """Create a new chat room with the user as owner."""

    task_id: str | None = Field(
        None, description="Optional task ID to associate with the chat."
    )
