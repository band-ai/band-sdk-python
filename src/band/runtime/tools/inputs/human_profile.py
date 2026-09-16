"""Human-tool input models: the user's own profile, and room peers.

See ``human_agents`` for the field-for-field-mirrors-band-mcp note that
applies to every human-tool input model.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GetMyProfileInput(BaseModel):
    """Get the current user's profile details.

    Returns your profile information including name, email, role, etc.
    """

    pass  # No parameters required.


class UpdateMyProfileInput(BaseModel):
    """Update the current user's profile."""

    first_name: str | None = Field(None, description="New first name (optional).")
    last_name: str | None = Field(None, description="New last name (optional).")


class ListMyPeersInput(BaseModel):
    """List entities you can interact with in chat rooms.

    Peers include other users, your agents, and global agents.
    """

    not_in_chat: str | None = Field(
        None,
        description="Exclude entities already in this chat room (optional).",
    )
    peer_type: str | None = Field(
        None, description="Filter by type: 'User' or 'Agent' (optional)."
    )
    page: int | None = Field(None, description="Page number (optional).")
    page_size: int | None = Field(None, description="Items per page (optional).")
