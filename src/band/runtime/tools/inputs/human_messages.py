"""Human-tool input models: chat message listing and sending.

See ``human_agents`` for the field-for-field-mirrors-band-mcp note that
applies to every human-tool input model.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from band.runtime.tools.inputs.chat import require_visible_content


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

    # The human-scope send posts through `human_api_messages`, which
    # `post_message` does not cover, so the platform's visible-content rule is
    # enforced here instead.
    _validate_content = field_validator("content")(require_visible_content)
