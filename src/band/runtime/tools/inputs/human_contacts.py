"""Human-tool input models: contacts and contact requests.

See ``human_agents`` for the field-for-field-mirrors-band-mcp note that
applies to every human-tool input model.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from band.core.types import ContactRequestSentStatus


class ListMyContactsInput(BaseModel):
    """List the user's contacts.

    Returns active contacts with their details including handle, email, and type.
    """

    page: int | None = Field(None, description="Page number for pagination (optional).")
    page_size: int | None = Field(
        None, description="Number of items per page (optional)."
    )


class CreateContactRequestInput(BaseModel):
    """Send a contact request to another user."""

    recipient_handle: str = Field(
        ...,
        description="Handle of the user to add (with or without @ prefix, required).",
    )
    message: str | None = Field(
        None,
        description="Optional message to include with the request (max 500 chars).",
    )


class ListReceivedContactRequestsInput(BaseModel):
    """List contact requests received by the user.

    Returns pending contact requests that need approval or rejection.
    """

    page: int | None = Field(None, description="Page number for pagination (optional).")
    page_size: int | None = Field(
        None, description="Number of items per page (optional)."
    )


class ListSentContactRequestsInput(BaseModel):
    """List contact requests sent by the user."""

    status: ContactRequestSentStatus | None = Field(
        None,
        description=(
            "Filter by status: 'pending', 'approved', 'rejected', "
            "'cancelled', or 'all' (optional)."
        ),
    )
    page: int | None = Field(None, description="Page number for pagination (optional).")
    page_size: int | None = Field(
        None, description="Number of items per page (optional)."
    )


class ApproveContactRequestInput(BaseModel):
    """Approve a received contact request."""

    request_id: str = Field(
        ..., description="The contact request ID to approve (required)."
    )


class RejectContactRequestInput(BaseModel):
    """Reject a received contact request."""

    request_id: str = Field(
        ..., description="The contact request ID to reject (required)."
    )


class CancelContactRequestInput(BaseModel):
    """Cancel a sent contact request."""

    request_id: str = Field(
        ..., description="The contact request ID to cancel (required)."
    )


class ResolveHandleInput(BaseModel):
    """Look up an entity by handle.

    Resolves a handle to its entity details. Use this to verify a handle
    exists before sending a contact request.
    """

    handle: str = Field(..., description="The handle to resolve (required).")


class RemoveMyContactInput(BaseModel):
    """Remove an existing contact.

    Removes a contact by either contact_id or handle. At least one must be provided.
    If both are provided, both are sent to the API (contact_id takes precedence).
    """

    contact_id: str | None = Field(
        None,
        description="The contact record ID (optional, provide this or handle).",
    )
    handle: str | None = Field(
        None,
        description="The contact's handle (optional, provide this or contact_id).",
    )
