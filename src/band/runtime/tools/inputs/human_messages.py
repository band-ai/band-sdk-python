"""Human-tool input models: chat message listing and sending.

See ``human_agents`` for the field-for-field-mirrors-band-mcp note that
applies to every human-tool input model.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ListMyChatMessagesInput(BaseModel):
    """List messages in a chat room."""

    chat_id: str = Field(..., description="The chat room ID (required).")
    page: int | None = Field(None, description="Page number (optional).")
    page_size: int | None = Field(None, description="Items per page (optional).")
    message_type: str | None = Field(
        None,
        description="Filter by type: 'text', 'tool_call', etc. (optional).",
    )
    since: str | None = Field(
        None,
        description="ISO 8601 timestamp to filter messages after (optional).",
    )


class SendMyChatMessageInput(BaseModel):
    """Send a message in a chat room."""

    chat_id: str = Field(..., description="The chat room ID (required).")
    content: str = Field(..., description="Message text (required).")
    recipients: str = Field(
        ...,
        description=(
            "Non-empty comma-separated participant names to @mention (required). "
            "Must contain at least one name; empty string is not accepted."
        ),
    )
