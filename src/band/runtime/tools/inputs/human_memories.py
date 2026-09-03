"""Human-tool input models: user-scoped memories.

See ``human_agents`` for the field-for-field-mirrors-band-mcp note that
applies to every human-tool input model.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ListUserMemoriesInput(BaseModel):
    """List memories available to the authenticated user."""

    chat_room_id: str | None = Field(None, description="Filter by chat room ID.")
    scope: str | None = Field(None, description="Filter by scope.")
    system: str | None = Field(None, description="Filter by memory system.")
    memory_type: str | None = Field(None, description="Filter by memory type.")
    segment: str | None = Field(None, description="Filter by segment.")
    content_query: str | None = Field(None, description="Full-text search query.")
    page_size: int | None = Field(None, description="Number of results per page.")
    status: str | None = Field(None, description="Filter by status.")


class GetUserMemoryInput(BaseModel):
    """Get a single user memory by ID."""

    memory_id: str = Field(..., description="Memory ID (required).")


class SupersedeUserMemoryInput(BaseModel):
    """Mark a user memory as superseded."""

    memory_id: str = Field(..., description="Memory ID (required).")


class ArchiveUserMemoryInput(BaseModel):
    """Archive a user memory."""

    memory_id: str = Field(..., description="Memory ID (required).")


class RestoreUserMemoryInput(BaseModel):
    """Restore an archived user memory."""

    memory_id: str = Field(..., description="Memory ID (required).")


class DeleteUserMemoryInput(BaseModel):
    """Delete a user memory permanently."""

    memory_id: str = Field(..., description="Memory ID (required).")
