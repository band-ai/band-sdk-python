"""
AgentTools - Tools for LLM platform interaction.

Bound to a room_id. Uses AsyncRestClient directly for API calls.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import AliasChoices, BaseModel, Field, ValidationError

from thenvoi.client.rest import ChatRoomRequest, DEFAULT_REQUEST_OPTIONS
from thenvoi.core.exceptions import ThenvoiToolError
from thenvoi.core.protocols import AgentToolsProtocol

if TYPE_CHECKING:
    from anthropic.types import ToolParam

    from thenvoi.client.rest import AsyncRestClient

    from .execution import ExecutionContext

logger = logging.getLogger(__name__)


def _normalize_handle(value: str) -> str:
    """Strip leading ``@`` so ``@alice`` and ``alice`` compare equal."""
    return value.lstrip("@").lower()


def _entity_field(entity: dict[str, Any] | Any, field: str) -> str:
    """Read a field from a dict or a Fern/Pydantic model, returning ``""`` on miss."""
    if isinstance(entity, dict):
        return entity.get(field) or ""
    return getattr(entity, field, None) or ""


def _matches_identifier(entity: dict[str, Any] | Any, identifier: str) -> bool:
    """Check if *identifier* matches an entity's handle, name, or ID (case-insensitive).

    Handles are compared after stripping the ``@`` prefix so that ``@alice``
    and ``alice`` are treated as equivalent.

    *entity* may be a plain dict (cached participant) or a Fern Pydantic model.
    """
    # Handle comparison — normalize both sides
    entity_handle = _entity_field(entity, "handle")
    if entity_handle and _normalize_handle(entity_handle) == _normalize_handle(
        identifier
    ):
        return True

    # Name and ID — plain case-insensitive comparison
    val = identifier.lower()
    for field in ("name", "id"):
        entity_val = _entity_field(entity, field)
        if entity_val and entity_val.lower() == val:
            return True
    return False


@dataclass(frozen=True)
class ToolDefinition:
    """Metadata for a built-in Thenvoi tool."""

    name: str
    input_model: type[BaseModel]
    method_name: str
    surface: Literal["agent", "human"] = "agent"


# --- Tool input models (single source of truth for schemas) ---


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
    message_type: Literal["thought", "error", "task"] = Field(
        ..., description="Type of event"
    )
    metadata: dict[str, Any] | None = Field(
        None, description="Optional structured data for the event"
    )


class AddParticipantInput(BaseModel):
    """Add a participant (agent or user) to the chat room.

    IMPORTANT: Use thenvoi_lookup_peers() first to find available agents.
    """

    identifier: str = Field(
        ...,
        alias="identifier",
        validation_alias=AliasChoices("identifier", "name"),
        description=(
            "Identifier of participant to add — can be a handle, name, or ID "
            "(from thenvoi_lookup_peers). Prefer the exact ID returned by "
            "thenvoi_lookup_peers; handles are mainly for mentions."
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
    Returns dict with 'peers' list and 'metadata' (page, page_size, total_count, total_pages).
    Use this to find specialized agents (e.g., Weather Agent) when you cannot answer
    a question directly.
    """

    page: int = Field(1, description="Page number")
    page_size: int = Field(50, le=100, description="Items per page (max 100)")


class GetParticipantsInput(BaseModel):
    """Get a list of all participants in the current chat room."""

    pass  # No parameters required


class CreateChatroomInput(BaseModel):
    """Create a new chat room for a specific task or conversation."""

    task_id: str | None = Field(
        default=None, description="Associated task ID (optional)"
    )


class ListContactsInput(BaseModel):
    """List agent's contacts with pagination."""

    page: int = Field(1, description="Page number", ge=1)
    page_size: int = Field(50, description="Items per page", ge=1, le=100)


class AddContactInput(BaseModel):
    """Send a contact request to add someone as a contact.

    Returns 'pending' when request is created.
    Returns 'approved' when inverse request existed and was auto-accepted.
    """

    handle: str = Field(
        ...,
        description="Handle of user/agent to add (e.g., '@john' or '@john/agent-name')",
    )
    message: str | None = Field(None, description="Optional message with the request")


class RemoveContactInput(BaseModel):
    """Remove an existing contact by handle or ID."""

    handle: str | None = Field(None, description="Contact's handle")
    contact_id: str | None = Field(None, description="Or contact record ID (UUID)")


class ListContactRequestsInput(BaseModel):
    """List both received and sent contact requests.

    Received requests are always filtered to pending status.
    Sent requests can be filtered by status.
    """

    page: int = Field(1, description="Page number", ge=1)
    page_size: int = Field(
        50, description="Items per page per direction (max 100)", ge=1, le=100
    )
    sent_status: Literal["pending", "approved", "rejected", "cancelled", "all"] = Field(
        "pending", description="Filter sent requests by status"
    )


class RespondContactRequestInput(BaseModel):
    """Respond to a contact request.

    Actions:
    - 'approve'/'reject': For requests you RECEIVED (handle = requester's handle)
    - 'cancel': For requests you SENT (handle = recipient's handle)
    """

    action: Literal["approve", "reject", "cancel"] = Field(
        ..., description="Action to take"
    )
    handle: str | None = Field(None, description="Other party's handle")
    request_id: str | None = Field(None, description="Or request ID (UUID)")


class ListMemoriesInput(BaseModel):
    """List memories accessible to the agent.

    Returns memories about the specified subject (cross-agent sharing)
    and organization-wide shared memories.
    """

    subject_id: str | None = Field(
        None, description="Filter by subject UUID (required for subject-scoped queries)"
    )
    scope: Literal["subject", "organization", "all"] | None = Field(
        None, description="Filter by scope"
    )
    system: Literal["sensory", "working", "long_term"] | None = Field(
        None, description="Filter by memory system"
    )
    type: (
        Literal["iconic", "echoic", "haptic", "episodic", "semantic", "procedural"]
        | None
    ) = Field(None, description="Filter by memory type")
    segment: Literal["user", "agent", "tool", "guideline"] | None = Field(
        None, description="Filter by segment"
    )
    content_query: str | None = Field(None, description="Full-text search query")
    page_size: int = Field(50, description="Number of results per page", ge=1, le=50)
    status: Literal["active", "superseded", "archived", "all"] | None = Field(
        None, description="Filter by status"
    )


class StoreMemoryInput(BaseModel):
    """Store a new memory entry.

    The memory will be associated with the authenticated agent as the source.
    For subject-scoped memories, provide a subject_id.
    For organization-scoped memories, omit subject_id.
    """

    content: str = Field(..., description="The memory content")
    system: Literal["sensory", "working", "long_term"] = Field(
        ..., description="Memory system tier"
    )
    type: Literal[
        "iconic", "echoic", "haptic", "episodic", "semantic", "procedural"
    ] = Field(..., description="Memory type (must be valid for selected system)")
    segment: Literal["user", "agent", "tool", "guideline"] = Field(
        ..., description="Logical segment"
    )
    thought: str = Field(..., description="Agent's reasoning for storing this memory")
    scope: Literal["subject", "organization"] = Field(
        "subject", description="Visibility scope"
    )
    subject_id: str | None = Field(
        None,
        description="UUID of the subject this memory is about (required for subject scope)",
    )
    metadata: dict[str, Any] | None = Field(
        None, description="Additional metadata (tags, references)"
    )


class GetMemoryInput(BaseModel):
    """Retrieve a specific memory by ID."""

    memory_id: str = Field(..., description="Memory ID (UUID)")


class SupersedeMemoryInput(BaseModel):
    """Mark a memory as superseded (soft delete).

    Use when information is outdated or incorrect.
    The memory remains for audit trail but won't appear in normal queries.
    Only the source agent can supersede.
    """

    memory_id: str = Field(..., description="Memory ID (UUID)")


class ArchiveMemoryInput(BaseModel):
    """Archive a memory (hide but preserve).

    Use when memory is valid but not currently needed.
    Archived memories can be restored later by humans.
    Only the source agent can archive.
    """

    memory_id: str = Field(..., description="Memory ID (UUID)")


# --- Human-tool input models (copied from thenvoi-mcp/src/thenvoi_mcp/tools/human/*.py) ---
#
# These models mirror the current thenvoi-mcp human tool handler signatures
# field-for-field. They are the canonical contract preserved by Phase 1 of
# INT-338: the observable tool surface stays identical to today's MCP
# behavior. Widening to full Fern parity is out of scope for this ticket.


# human_agents.py


class ListMyAgentsInput(BaseModel):
    """List agents owned by the user."""

    page: int | None = Field(None, description="Page number (optional).")
    page_size: int | None = Field(None, description="Items per page (optional).")


class RegisterMyAgentInput(BaseModel):
    """Register a new external agent.

    Returns the agent details including API key. Save the API key - it's only shown once!
    """

    name: str = Field(..., description="Agent name (required).")
    description: str = Field(..., description="Agent description (required).")


# human_chats.py


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


# human_contacts.py


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

    status: Literal["pending", "approved", "rejected", "cancelled", "all"] | None = (
        Field(
            None,
            description=(
                "Filter by status: 'pending', 'approved', 'rejected', "
                "'cancelled', or 'all' (optional)."
            ),
        )
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


# human_messages.py


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


# human_participants.py


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


# human_memories.py


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


# human_profile.py / human_peers


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


# Registry mapping tool names to their schemas and bound AgentTools methods.
TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "thenvoi_send_message": ToolDefinition(
        name="thenvoi_send_message",
        input_model=SendMessageInput,
        method_name="send_message",
    ),
    "thenvoi_send_event": ToolDefinition(
        name="thenvoi_send_event",
        input_model=SendEventInput,
        method_name="send_event",
    ),
    "thenvoi_add_participant": ToolDefinition(
        name="thenvoi_add_participant",
        input_model=AddParticipantInput,
        method_name="add_participant",
    ),
    "thenvoi_remove_participant": ToolDefinition(
        name="thenvoi_remove_participant",
        input_model=RemoveParticipantInput,
        method_name="remove_participant",
    ),
    "thenvoi_lookup_peers": ToolDefinition(
        name="thenvoi_lookup_peers",
        input_model=LookupPeersInput,
        method_name="lookup_peers",
    ),
    "thenvoi_get_participants": ToolDefinition(
        name="thenvoi_get_participants",
        input_model=GetParticipantsInput,
        method_name="get_participants",
    ),
    "thenvoi_create_chatroom": ToolDefinition(
        name="thenvoi_create_chatroom",
        input_model=CreateChatroomInput,
        method_name="create_chatroom",
    ),
    "thenvoi_list_contacts": ToolDefinition(
        name="thenvoi_list_contacts",
        input_model=ListContactsInput,
        method_name="list_contacts",
    ),
    "thenvoi_add_contact": ToolDefinition(
        name="thenvoi_add_contact",
        input_model=AddContactInput,
        method_name="add_contact",
    ),
    "thenvoi_remove_contact": ToolDefinition(
        name="thenvoi_remove_contact",
        input_model=RemoveContactInput,
        method_name="remove_contact",
    ),
    "thenvoi_list_contact_requests": ToolDefinition(
        name="thenvoi_list_contact_requests",
        input_model=ListContactRequestsInput,
        method_name="list_contact_requests",
    ),
    "thenvoi_respond_contact_request": ToolDefinition(
        name="thenvoi_respond_contact_request",
        input_model=RespondContactRequestInput,
        method_name="respond_contact_request",
    ),
    "thenvoi_list_memories": ToolDefinition(
        name="thenvoi_list_memories",
        input_model=ListMemoriesInput,
        method_name="list_memories",
    ),
    "thenvoi_store_memory": ToolDefinition(
        name="thenvoi_store_memory",
        input_model=StoreMemoryInput,
        method_name="store_memory",
    ),
    "thenvoi_get_memory": ToolDefinition(
        name="thenvoi_get_memory",
        input_model=GetMemoryInput,
        method_name="get_memory",
    ),
    "thenvoi_supersede_memory": ToolDefinition(
        name="thenvoi_supersede_memory",
        input_model=SupersedeMemoryInput,
        method_name="supersede_memory",
    ),
    "thenvoi_archive_memory": ToolDefinition(
        name="thenvoi_archive_memory",
        input_model=ArchiveMemoryInput,
        method_name="archive_memory",
    ),
    # --- Human tools (surface="human") ---
    # One entry per method in the Phase 1 human-tool mapping table.
    # Method names match HumanTools attributes; hasattr(HumanTools, method_name)
    # must resolve for every surface="human" definition.
    "thenvoi_list_my_agents": ToolDefinition(
        name="thenvoi_list_my_agents",
        input_model=ListMyAgentsInput,
        method_name="list_my_agents",
        surface="human",
    ),
    "thenvoi_register_my_agent": ToolDefinition(
        name="thenvoi_register_my_agent",
        input_model=RegisterMyAgentInput,
        method_name="register_my_agent",
        surface="human",
    ),
    "thenvoi_list_my_chats": ToolDefinition(
        name="thenvoi_list_my_chats",
        input_model=ListMyChatsInput,
        method_name="list_my_chats",
        surface="human",
    ),
    "thenvoi_create_my_chat_room": ToolDefinition(
        name="thenvoi_create_my_chat_room",
        input_model=CreateMyChatRoomInput,
        method_name="create_my_chat_room",
        surface="human",
    ),
    "thenvoi_get_my_chat_room": ToolDefinition(
        name="thenvoi_get_my_chat_room",
        input_model=GetMyChatRoomInput,
        method_name="get_my_chat_room",
        surface="human",
    ),
    "thenvoi_list_my_contacts": ToolDefinition(
        name="thenvoi_list_my_contacts",
        input_model=ListMyContactsInput,
        method_name="list_my_contacts",
        surface="human",
    ),
    "thenvoi_create_contact_request": ToolDefinition(
        name="thenvoi_create_contact_request",
        input_model=CreateContactRequestInput,
        method_name="create_contact_request",
        surface="human",
    ),
    "thenvoi_list_received_contact_requests": ToolDefinition(
        name="thenvoi_list_received_contact_requests",
        input_model=ListReceivedContactRequestsInput,
        method_name="list_received_contact_requests",
        surface="human",
    ),
    "thenvoi_list_sent_contact_requests": ToolDefinition(
        name="thenvoi_list_sent_contact_requests",
        input_model=ListSentContactRequestsInput,
        method_name="list_sent_contact_requests",
        surface="human",
    ),
    "thenvoi_approve_contact_request": ToolDefinition(
        name="thenvoi_approve_contact_request",
        input_model=ApproveContactRequestInput,
        method_name="approve_contact_request",
        surface="human",
    ),
    "thenvoi_reject_contact_request": ToolDefinition(
        name="thenvoi_reject_contact_request",
        input_model=RejectContactRequestInput,
        method_name="reject_contact_request",
        surface="human",
    ),
    "thenvoi_cancel_contact_request": ToolDefinition(
        name="thenvoi_cancel_contact_request",
        input_model=CancelContactRequestInput,
        method_name="cancel_contact_request",
        surface="human",
    ),
    "thenvoi_resolve_handle": ToolDefinition(
        name="thenvoi_resolve_handle",
        input_model=ResolveHandleInput,
        method_name="resolve_handle",
        surface="human",
    ),
    "thenvoi_remove_my_contact": ToolDefinition(
        name="thenvoi_remove_my_contact",
        input_model=RemoveMyContactInput,
        method_name="remove_my_contact",
        surface="human",
    ),
    "thenvoi_list_my_chat_messages": ToolDefinition(
        name="thenvoi_list_my_chat_messages",
        input_model=ListMyChatMessagesInput,
        method_name="list_my_chat_messages",
        surface="human",
    ),
    "thenvoi_send_my_chat_message": ToolDefinition(
        name="thenvoi_send_my_chat_message",
        input_model=SendMyChatMessageInput,
        method_name="send_my_chat_message",
        surface="human",
    ),
    "thenvoi_list_my_chat_participants": ToolDefinition(
        name="thenvoi_list_my_chat_participants",
        input_model=ListMyChatParticipantsInput,
        method_name="list_my_chat_participants",
        surface="human",
    ),
    "thenvoi_add_my_chat_participant": ToolDefinition(
        name="thenvoi_add_my_chat_participant",
        input_model=AddMyChatParticipantInput,
        method_name="add_my_chat_participant",
        surface="human",
    ),
    "thenvoi_remove_my_chat_participant": ToolDefinition(
        name="thenvoi_remove_my_chat_participant",
        input_model=RemoveMyChatParticipantInput,
        method_name="remove_my_chat_participant",
        surface="human",
    ),
    "thenvoi_list_user_memories": ToolDefinition(
        name="thenvoi_list_user_memories",
        input_model=ListUserMemoriesInput,
        method_name="list_user_memories",
        surface="human",
    ),
    "thenvoi_get_user_memory": ToolDefinition(
        name="thenvoi_get_user_memory",
        input_model=GetUserMemoryInput,
        method_name="get_user_memory",
        surface="human",
    ),
    "thenvoi_supersede_user_memory": ToolDefinition(
        name="thenvoi_supersede_user_memory",
        input_model=SupersedeUserMemoryInput,
        method_name="supersede_user_memory",
        surface="human",
    ),
    "thenvoi_archive_user_memory": ToolDefinition(
        name="thenvoi_archive_user_memory",
        input_model=ArchiveUserMemoryInput,
        method_name="archive_user_memory",
        surface="human",
    ),
    "thenvoi_restore_user_memory": ToolDefinition(
        name="thenvoi_restore_user_memory",
        input_model=RestoreUserMemoryInput,
        method_name="restore_user_memory",
        surface="human",
    ),
    "thenvoi_delete_user_memory": ToolDefinition(
        name="thenvoi_delete_user_memory",
        input_model=DeleteUserMemoryInput,
        method_name="delete_user_memory",
        surface="human",
    ),
    "thenvoi_get_my_profile": ToolDefinition(
        name="thenvoi_get_my_profile",
        input_model=GetMyProfileInput,
        method_name="get_my_profile",
        surface="human",
    ),
    "thenvoi_update_my_profile": ToolDefinition(
        name="thenvoi_update_my_profile",
        input_model=UpdateMyProfileInput,
        method_name="update_my_profile",
        surface="human",
    ),
    "thenvoi_list_my_peers": ToolDefinition(
        name="thenvoi_list_my_peers",
        input_model=ListMyPeersInput,
        method_name="list_my_peers",
        surface="human",
    ),
}

TOOL_MODELS: dict[str, type[BaseModel]] = {
    name: definition.input_model
    for name, definition in TOOL_DEFINITIONS.items()
    if definition.surface == "agent"
}

# Memory tools - optional, only available for enterprise customers.
# Explicitly listed (not derived by heuristic) because memory is an opt-in
# enterprise feature and accidental inclusion of a non-memory tool would
# expose functionality that should be gated.
MEMORY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "thenvoi_list_memories",
        "thenvoi_store_memory",
        "thenvoi_get_memory",
        "thenvoi_supersede_memory",
        "thenvoi_archive_memory",
    }
)

# Contact tools - explicitly listed (not derived by heuristic) because a
# future tool whose name happens to contain "contact" (e.g.
# thenvoi_get_contact_context) would be silently misclassified.
CONTACT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "thenvoi_list_contacts",
        "thenvoi_add_contact",
        "thenvoi_remove_contact",
        "thenvoi_list_contact_requests",
        "thenvoi_respond_contact_request",
    }
)

# Human-surface memory tools - parallel to MEMORY_TOOL_NAMES but on the
# ``surface="human"`` side of the registry. Used by iter_tool_definitions()
# to apply the ``include_memory`` filter uniformly across both surfaces.
HUMAN_MEMORY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "thenvoi_list_user_memories",
        "thenvoi_get_user_memory",
        "thenvoi_supersede_user_memory",
        "thenvoi_archive_user_memory",
        "thenvoi_restore_user_memory",
        "thenvoi_delete_user_memory",
    }
)

# Human-surface contact tools - parallel to CONTACT_TOOL_NAMES.
HUMAN_CONTACT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "thenvoi_list_my_contacts",
        "thenvoi_create_contact_request",
        "thenvoi_list_received_contact_requests",
        "thenvoi_list_sent_contact_requests",
        "thenvoi_approve_contact_request",
        "thenvoi_reject_contact_request",
        "thenvoi_cancel_contact_request",
        "thenvoi_resolve_handle",
        "thenvoi_remove_my_contact",
    }
)

# Derived from TOOL_MODELS — single source of truth
ALL_TOOL_NAMES: frozenset[str] = frozenset(TOOL_MODELS.keys())

# Fail fast on typos — catch at import time, not in a test run.
# Use explicit checks instead of ``assert`` so they are not stripped by -O.
if MEMORY_TOOL_NAMES - ALL_TOOL_NAMES:
    raise ValueError(f"Unknown memory tools: {MEMORY_TOOL_NAMES - ALL_TOOL_NAMES}")
if CONTACT_TOOL_NAMES - ALL_TOOL_NAMES:
    raise ValueError(f"Unknown contact tools: {CONTACT_TOOL_NAMES - ALL_TOOL_NAMES}")

# Human-surface registry membership is validated against TOOL_DEFINITIONS
# (not TOOL_MODELS, which stays agent-only for back-compat).
_ALL_DEFINITION_NAMES: frozenset[str] = frozenset(TOOL_DEFINITIONS.keys())
if HUMAN_MEMORY_TOOL_NAMES - _ALL_DEFINITION_NAMES:
    raise ValueError(
        f"Unknown human memory tools: {HUMAN_MEMORY_TOOL_NAMES - _ALL_DEFINITION_NAMES}"
    )
if HUMAN_CONTACT_TOOL_NAMES - _ALL_DEFINITION_NAMES:
    raise ValueError(
        "Unknown human contact tools: "
        f"{HUMAN_CONTACT_TOOL_NAMES - _ALL_DEFINITION_NAMES}"
    )

BASE_TOOL_NAMES: frozenset[str] = ALL_TOOL_NAMES - MEMORY_TOOL_NAMES
CHAT_TOOL_NAMES: frozenset[str] = BASE_TOOL_NAMES - CONTACT_TOOL_NAMES
MCP_TOOL_PREFIX: str = "mcp__thenvoi__"


def mcp_tool_names(names: frozenset[str]) -> list[str]:
    """Convert base tool names to MCP-prefixed names for Claude SDK.

    Returns a sorted list for deterministic ordering across runs.
    """
    return [f"{MCP_TOOL_PREFIX}{name}" for name in sorted(names)]


def get_tool_description(name: str) -> str:
    """
    Get the LLM-optimized description for a tool.

    Use this to get consistent tool descriptions across all adapters.
    Descriptions are sourced from the Pydantic model docstrings.

    Args:
        name: Tool name (e.g., "thenvoi_send_message", "thenvoi_lookup_peers")
              Also accepts unprefixed names for backwards compatibility (deprecated).

    Returns:
        Tool description string
    """
    # Try exact match first
    model = TOOL_MODELS.get(name)
    if model and model.__doc__:
        return model.__doc__

    # Try with prefix for backwards compatibility (deprecated)
    if not name.startswith("thenvoi_"):
        prefixed_name = f"thenvoi_{name}"
        model = TOOL_MODELS.get(prefixed_name)
        if model and model.__doc__:
            warnings.warn(
                f"Tool name '{name}' is deprecated. Use '{prefixed_name}' instead.",
                DeprecationWarning,
                stacklevel=2,
            )
            return model.__doc__

    return f"Execute {name}"


def iter_tool_definitions(
    *,
    surface: Literal["agent", "human"] | None = "agent",
    include_memory: bool = False,
    include_contacts: bool = True,
) -> list[ToolDefinition]:
    """Return built-in tool definitions with optional category filtering.

    The three filters compose as independent predicates:

    - ``surface``: when not ``None``, restrict to definitions whose
      ``ToolDefinition.surface`` equals the given value. ``"agent"``
      (default) yields only agent tools, ``"human"`` yields only human
      tools, and ``None`` yields both surfaces. The default is pinned to
      ``"agent"`` so existing callers (``claude_sdk``, ``opencode``,
      ``acp``) that pipe the result straight into ``AgentTools``-shaped
      backends don't silently gain ``HumanTools``-bound entries.
    - ``include_memory``: if ``False`` (default), drop memory tools. This
      applies to both the agent ``MEMORY_TOOL_NAMES`` set and the human
      memory tools (``thenvoi_list_user_memories``, etc.).
    - ``include_contacts``: if ``False``, drop contact tools. This applies
      to both the agent ``CONTACT_TOOL_NAMES`` set and the human contact
      tools (``thenvoi_list_my_contacts``, etc.).

    Args:
        surface: Optional surface filter (``"agent"`` or ``"human"``).
            Default ``"agent"``. Pass ``None`` explicitly to opt in to a
            union view across both surfaces.
        include_memory: Include memory tools (enterprise). Default False.
        include_contacts: Include contact-management tools. Default True for
            backward compatibility. Pass False to gate contact tools behind
            ``Capability.CONTACTS``. The hub-room execution path always
            forces this to True regardless of adapter preference (see
            ``AgentTools.get_tool_schemas`` HUB_ROOM auto-enable rule).
    """
    excluded: set[str] = set()
    if not include_memory:
        excluded |= MEMORY_TOOL_NAMES
        excluded |= HUMAN_MEMORY_TOOL_NAMES
    if not include_contacts:
        excluded |= CONTACT_TOOL_NAMES
        excluded |= HUMAN_CONTACT_TOOL_NAMES

    results: list[ToolDefinition] = []
    for definition in TOOL_DEFINITIONS.values():
        if surface is not None and definition.surface != surface:
            continue
        if definition.name in excluded:
            continue
        results.append(definition)
    return results


def format_tool_validation_error(tool_name: str, error: ValidationError) -> str:
    """Format Pydantic validation errors for LLM-readable tool feedback."""
    errors = [
        f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}"
        for err in error.errors()
    ]
    return f"Invalid arguments for {tool_name}: {', '.join(errors)}"


def validate_tool_arguments(
    tool_name: str,
    input_model: type[BaseModel],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Validate tool arguments and return a normalized kwargs dictionary."""
    try:
        validated = input_model.model_validate(arguments)
    except ValidationError as error:
        raise ValueError(format_tool_validation_error(tool_name, error)) from error

    return validated.model_dump(exclude_none=True)


class AgentTools(AgentToolsProtocol):
    """
    Room-bound tools for LLM platform interaction.

    Uses AsyncRestClient directly for API calls.
    Bound to a specific room_id. Passed to execution handlers.

    This class provides:
    - Tool methods (send_message, add_participant, etc.)
    - Contact management methods (list_contacts, add_contact, etc.)
    - Schema converters for different LLM frameworks
    - execute_tool_call() for programmatic dispatch

    Note: AgentTools vs ContactTools
        - AgentTools: Room-bound. Used by LLM agents in chat rooms.
          Has full tool suite including messaging, participants, AND contacts.
        - ContactTools: Agent-level. Used by ContactEventHandler for
          programmatic contact handling in CALLBACK strategy. Contact-only.

    Example (from ExecutionContext):
        tools = AgentTools.from_context(ctx)
        await tools.send_message("Hello!", mentions=["@john"])

    Example (manual construction):
        tools = AgentTools(room_id, rest_client, participants=[...])
        schemas = tools.get_tool_schemas("anthropic")
    """

    def __init__(
        self,
        room_id: str,
        rest: "AsyncRestClient",
        participants: list[dict[str, Any]] | None = None,
        *,
        hub_room_id: str | None = None,
    ):
        """
        Initialize AgentTools for a specific room.

        Args:
            room_id: The room this tools instance is bound to
            rest: AsyncRestClient for API calls
            participants: Optional list of participants for mention resolution
            hub_room_id: Optional hub-room ID. When this AgentTools instance
                is bound to the hub room (room_id == hub_room_id), the
                contact-management tool schemas are force-included regardless
                of the ``include_contacts`` argument to schema methods. The
                hub-room system prompt instructs the LLM to call contact
                tools, so they must be exposed even if the adapter would
                otherwise gate them.
        """
        self.room_id = room_id
        self.rest = rest
        self._participants = participants or []
        self._hub_room_id = hub_room_id
        self._ctx: ExecutionContext | None = None

    @property
    def participants(self) -> list[dict[str, Any]]:
        """Return a shallow copy of the cached participant list."""
        return list(self._participants)

    @classmethod
    def from_context(cls, ctx: "ExecutionContext") -> "AgentTools":
        """
        Create AgentTools from an ExecutionContext.

        Convenience method for SDK-heavy users.

        Args:
            ctx: ExecutionContext to create tools from

        Returns:
            AgentTools instance bound to the context's room
        """
        tools = cls(
            ctx.room_id,
            ctx.link.rest,
            ctx.participants,
            hub_room_id=getattr(ctx, "hub_room_id", None),
        )
        tools._ctx = ctx
        return tools

    # --- Tool methods ---

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> Any:
        """
        Send a message to the current room.

        Args:
            content: Message content to send
            mentions: List of participant handles (strings). SDK resolves handles to IDs.
                      Format: @<username> for users, @<username>/<agent-name> for agents.
                      Passing list[dict[str, str]] is deprecated; use list[str] instead.

        Returns:
            Fern ChatMessage model (Pydantic). Serialized to dict by
            execute_tool_call() at the adapter boundary.

        Raises:
            ValueError: If a mentioned handle is not found in participants
        """
        from thenvoi.client.rest import (
            ChatMessageRequest,
            ChatMessageRequestMentionsItem,
        )

        # Deprecation warning for dict-style mentions
        if mentions and isinstance(mentions[0], dict):
            warnings.warn(
                "Passing mentions as list[dict] is deprecated. "
                "Use list[str] with handles instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        resolved_mentions = self._resolve_mentions(mentions or [])

        # Validate mentions are not empty — API requires ≥1 mention.
        # Return a helpful error so the LLM can retry with proper mentions.
        if not resolved_mentions:
            participant_names = [
                p.get("handle") or p["name"] for p in self._participants
            ]
            raise ThenvoiToolError(
                "At least one mention is required. "
                f"Available participants: {participant_names}. "
                "Please retry with mentions specifying who this message is for."
            )

        logger.debug("Sending message to room %s", self.room_id)

        # Convert to API format - use handle (not name) for mentions
        mention_items = [
            ChatMessageRequestMentionsItem(id=m["id"], handle=m["handle"])
            for m in resolved_mentions
        ]

        response = await self.rest.agent_api_messages.create_agent_chat_message(
            chat_id=self.room_id,
            message=ChatMessageRequest(content=content, mentions=mention_items),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        if not response.data:
            raise RuntimeError("Failed to send message - no response data")
        return response.data

    async def send_event(
        self,
        content: str,
        message_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """
        Send an event to the current room.

        Events don't require mentions - use for tool_call, tool_result, error, thought, task.

        Args:
            content: Human-readable event content
            message_type: One of: tool_call, tool_result, thought, error, task
            metadata: Optional structured data for the event

        Returns:
            Fern ChatEvent model (Pydantic). Serialized to dict by
            execute_tool_call() at the adapter boundary.
        """
        from thenvoi.client.rest import ChatEventRequest

        logger.debug("Sending %s event to room %s", message_type, self.room_id)

        response = await self.rest.agent_api_events.create_agent_chat_event(
            chat_id=self.room_id,
            event=ChatEventRequest(
                content=content,
                message_type=message_type,
                metadata=metadata,
            ),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        if not response.data:
            raise RuntimeError("Failed to send event - no response data")
        return response.data

    async def create_chatroom(self, task_id: str | None = None) -> str:
        """
        Create a new chat room.

        Args:
            task_id: Associated task ID (optional)

        Returns:
            Room ID of the created room
        """
        logger.debug("Creating chatroom with task_id=%s", task_id)
        response = await self.rest.agent_api_chats.create_agent_chat(
            chat=ChatRoomRequest(task_id=task_id),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        return response.data.id

    async def fetch_room_context(
        self,
        *,
        room_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Fetch agent-relevant room messages, paginated.

        Returns messages this agent sent or was mentioned in, ordered oldest
        first. Used by state-reconstruction adapters (e.g. CrewAI Flow) to
        rebuild durable run state from task events.
        """
        from thenvoi.runtime._context_serialization import context_item_to_dict

        response = await self.rest.agent_api_context.get_agent_chat_context(
            chat_id=room_id,
            page=page,
            page_size=page_size,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        data = [context_item_to_dict(item) for item in (response.data or [])]
        meta = getattr(response, "meta", None)
        if meta is None:
            meta_dict: dict[str, Any] = {
                "page": page,
                "page_size": page_size,
                "total_count": len(data),
                "total_pages": 1 if data else 0,
            }
        elif hasattr(meta, "model_dump"):
            meta_dict = meta.model_dump()
        else:
            meta_dict = {
                "page": getattr(meta, "page", page),
                "page_size": getattr(meta, "page_size", page_size),
                "total_count": getattr(meta, "total_count", len(data)),
                "total_pages": getattr(meta, "total_pages", 1 if data else 0),
            }
        return {"data": data, "meta": meta_dict}

    async def add_participant(
        self, identifier: str, role: str = "member"
    ) -> dict[str, Any]:
        """
        Add a participant to the current room.

        Args:
            identifier: Handle, name, or ID of the participant to add
            role: Role in room - "owner", "admin", or "member" (default)

        Returns:
            Dict with added participant info (id, name, role, status)

        Raises:
            ValueError: If participant not found
        """
        from thenvoi.client.rest import ParticipantRequest

        logger.debug(
            "Adding participant '%s' with role '%s' to room %s",
            identifier,
            role,
            self.room_id,
        )

        # First check if participant is already in the room. Always prefer a
        # fresh server snapshot to avoid stale-cache decisions after room
        # updates — get_participants() refreshes self._participants for us.
        await self.get_participants()

        for cached in self._participants:
            if _matches_identifier(cached, identifier):
                cached_id = cached.get("id")
                if not cached_id:
                    raise ValueError(f"Participant '{identifier}' has no ID.")
                logger.debug("Participant '%s' is already in the room", identifier)
                return {
                    "id": cached_id,
                    "name": cached.get("name", identifier),
                    "role": role,
                    "status": "already_in_room",
                }

        # Look up participant by identifier (paginates through all peers)
        participant = await self._lookup_peer(identifier)
        if not participant:
            raise ValueError(
                f"Participant '{identifier}' not found. "
                "Use thenvoi_lookup_peers to find available peers."
            )

        participant_id = participant.id
        participant_name = getattr(participant, "name", None) or identifier
        logger.debug("Resolved '%s' to ID: %s", identifier, participant_id)

        await self.rest.agent_api_participants.add_agent_chat_participant(
            chat_id=self.room_id,
            participant=ParticipantRequest(participant_id=participant_id, role=role),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

        # Update internal participant cache for immediate mention resolution
        # NOTE: WebSocket will eventually deliver participant_added event, but this
        # allows @mentions to work immediately after add_participant returns.
        new_participant = {
            "id": participant_id,
            "name": participant_name,
            "type": getattr(participant, "type", "Agent"),
            "handle": getattr(participant, "handle", None),
        }
        self._participants.append(new_participant)
        # Sync back to ExecutionContext so future turns see the update
        if self._ctx is not None:
            self._ctx.add_participant(new_participant)
        logger.debug(
            "Updated participant cache: added %s, total=%s",
            participant_name,
            len(self._participants),
        )

        return {
            "id": participant_id,
            "name": participant_name,
            "role": role,
            "status": "added",
        }

    async def remove_participant(self, identifier: str) -> dict[str, Any]:
        """
        Remove a participant from the current room.

        Args:
            identifier: Handle, name, or ID of the participant to remove

        Returns:
            Dict with removed participant info (id, name, status)

        Raises:
            ValueError: If participant not found in room
        """
        logger.debug("Removing participant '%s' from room %s", identifier, self.room_id)

        # Look up participant by identifier. Always prefer a fresh server
        # snapshot to avoid stale-cache decisions after room updates —
        # get_participants() refreshes self._participants for us.
        await self.get_participants()

        participant: dict[str, Any] | None = None
        for cached in self._participants:
            if _matches_identifier(cached, identifier):
                participant = cached
                break

        if not participant:
            raise ValueError(f"Participant '{identifier}' not found in this room.")

        participant_id = participant.get("id")
        if not participant_id:
            raise ValueError(f"Participant '{identifier}' has no ID.")
        participant_name = participant.get("name", identifier)
        logger.debug("Resolved '%s' to ID: %s", identifier, participant_id)

        await self.rest.agent_api_participants.remove_agent_chat_participant(
            self.room_id,
            participant_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

        # Update internal participant cache
        # NOTE: WebSocket will eventually deliver participant_removed event, but this
        # prevents @mentions to the removed participant immediately after removal.
        self._participants = [
            p for p in self._participants if p.get("id") != participant_id
        ]
        # Sync back to ExecutionContext so future turns see the update
        if self._ctx is not None:
            self._ctx.remove_participant(participant_id)
        logger.debug(
            "Updated participant cache: removed %s, total=%s",
            participant_name,
            len(self._participants),
        )

        return {
            "id": participant_id,
            "name": participant_name,
            "status": "removed",
        }

    async def lookup_peers(self, page: int = 1, page_size: int = 50) -> Any:
        """
        Find available peers (agents and users) on the platform.

        Automatically filters to peers NOT already in the current room.

        Args:
            page: Page number (default 1)
            page_size: Items per page (default 50, max 100)

        Returns:
            Fern ListAgentPeersResponse (Pydantic) with .data (list of peers)
            and .metadata (pagination info). Serialized to dict by
            execute_tool_call() at the adapter boundary.
        """
        logger.debug("Looking up peers: page=%s, page_size=%s", page, page_size)
        response = await self.rest.agent_api_peers.list_agent_peers(
            page=page,
            page_size=page_size,
            not_in_chat=self.room_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

        return response

    async def get_participants(self) -> Any:
        """
        Get participants in the current room.

        Returns:
            List of Fern ChatParticipant models (Pydantic). Serialized to
            list[dict] by execute_tool_call() at the adapter boundary.
        """
        logger.debug("Getting participants for room %s", self.room_id)
        response = await self.rest.agent_api_participants.list_agent_chat_participants(
            chat_id=self.room_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

        # Treat ``data is None`` as a transient/unexpected response and preserve
        # the existing cache — every room the agent is in should at minimum
        # contain the agent itself, so ``None`` is not a legitimate "empty room".
        if response.data is None:
            logger.warning(
                "list_agent_chat_participants returned None for room %s; "
                "preserving cached participants",
                self.room_id,
            )
            return []

        # Refresh the internal cache so _resolve_mentions() sees participants
        # the LLM just discovered in this turn, even if they joined after
        # AgentTools was constructed. Without this, the LLM can call
        # get_participants, see a new participant, then fail to @mention them.
        refreshed = [
            {
                "id": p.id,
                "name": p.name,
                "type": p.type,
                "handle": getattr(p, "handle", None),
            }
            for p in response.data
        ]

        # Sync diff back to ExecutionContext so the refresh survives turn
        # boundaries. Without this, a new AgentTools built via from_context()
        # on the next turn would revert to the old participant snapshot.
        if self._ctx is not None:
            old_ids = {p.get("id") for p in self._participants if p.get("id")}
            new_ids = {p["id"] for p in refreshed if p["id"]}
            for participant_id in old_ids - new_ids:
                self._ctx.remove_participant(participant_id)
            for participant in refreshed:
                if participant["id"] and participant["id"] not in old_ids:
                    self._ctx.add_participant(participant)

        self._participants = refreshed
        return response.data

    # --- Contact management tools ---

    async def list_contacts(self, page: int = 1, page_size: int = 50) -> Any:
        """
        List agent's contacts with pagination.

        Args:
            page: Page number (default 1)
            page_size: Items per page (default 50, max 100)

        Returns:
            Fern ListAgentContactsResponse (Pydantic) with .data and .metadata.
            Serialized to dict by execute_tool_call() at the adapter boundary.
        """
        logger.debug("Listing contacts: page=%s, page_size=%s", page, page_size)
        response = await self.rest.agent_api_contacts.list_agent_contacts(
            page=page, page_size=page_size
        )

        return response

    async def add_contact(self, handle: str, message: str | None = None) -> Any:
        """
        Send a contact request to add someone as a contact.

        Args:
            handle: Handle of user/agent to add (e.g., '@john' or '@john/agent-name')
            message: Optional message with the request

        Returns:
            Fern model with id and status ('pending' or 'approved').
            Serialized to dict by execute_tool_call() at the adapter boundary.
        """
        logger.debug("Adding contact: handle=%s", handle)
        response = await self.rest.agent_api_contacts.add_agent_contact(
            handle=handle, message=message
        )
        if not response.data:
            raise RuntimeError("Failed to add contact - no response data")
        return response.data

    async def remove_contact(
        self, handle: str | None = None, contact_id: str | None = None
    ) -> Any:
        """
        Remove an existing contact by handle or ID.

        Args:
            handle: Contact's handle
            contact_id: Or contact record ID (UUID)

        Returns:
            Fern model with status ('removed').
            Serialized to dict by execute_tool_call() at the adapter boundary.

        Raises:
            ValueError: If neither handle nor contact_id is provided
        """
        if handle is None and contact_id is None:
            raise ValueError("Either handle or contact_id must be provided")

        logger.debug("Removing contact: handle=%s, contact_id=%s", handle, contact_id)

        # Build kwargs dynamically to avoid sending null values
        # The REST client uses OMIT for optional params, but passing None sends null
        kwargs: dict[str, Any] = {}
        if handle is not None:
            kwargs["handle"] = handle
        if contact_id is not None:
            kwargs["contact_id"] = contact_id

        response = await self.rest.agent_api_contacts.remove_agent_contact(**kwargs)
        if not response.data:
            raise RuntimeError("Failed to remove contact - no response data")
        return response.data

    async def list_contact_requests(
        self, page: int = 1, page_size: int = 50, sent_status: str = "pending"
    ) -> Any:
        """
        List both received and sent contact requests.

        Args:
            page: Page number (default 1)
            page_size: Items per page per direction (default 50, max 100)
            sent_status: Filter sent requests by status (default 'pending')

        Returns:
            Fern ListAgentContactRequestsResponse (Pydantic) with .data
            (.received, .sent) and .metadata. Serialized to dict by
            execute_tool_call() at the adapter boundary.
        """
        logger.debug(
            "Listing contact requests: page=%s, page_size=%s, sent_status=%s",
            page,
            page_size,
            sent_status,
        )
        response = await self.rest.agent_api_contacts.list_agent_contact_requests(
            page=page, page_size=page_size, sent_status=sent_status
        )

        return response

    async def respond_contact_request(
        self, action: str, handle: str | None = None, request_id: str | None = None
    ) -> Any:
        """
        Respond to a contact request (approve, reject, or cancel).

        Args:
            action: Action to take ('approve', 'reject', 'cancel')
            handle: Other party's handle
            request_id: Or request ID (UUID)

        Returns:
            Fern model with id and status.
            Serialized to dict by execute_tool_call() at the adapter boundary.

        Raises:
            ValueError: If neither handle nor request_id is provided
        """
        if handle is None and request_id is None:
            raise ValueError("Either handle or request_id must be provided")

        logger.debug(
            "Responding to contact request: action=%s, handle=%s, request_id=%s",
            action,
            handle,
            request_id,
        )

        # Build kwargs dynamically to avoid sending null values
        # The REST client uses OMIT for optional params, but passing None sends null
        kwargs: dict[str, Any] = {"action": action}
        if handle is not None:
            kwargs["handle"] = handle
        if request_id is not None:
            kwargs["request_id"] = request_id

        response = await self.rest.agent_api_contacts.respond_to_agent_contact_request(
            **kwargs
        )
        if not response.data:
            raise RuntimeError(
                "Failed to respond to contact request - no response data"
            )
        return response.data

    # --- Memory management tools ---

    async def list_memories(
        self,
        subject_id: str | None = None,
        scope: str | None = None,
        system: str | None = None,
        type: str | None = None,
        segment: str | None = None,
        content_query: str | None = None,
        page_size: int = 50,
        status: str | None = None,
    ) -> Any:
        """
        List memories accessible to the agent.

        Args:
            subject_id: Filter by subject UUID
            scope: Filter by scope (subject, organization, all)
            system: Filter by memory system (sensory, working, long_term)
            type: Filter by memory type
            segment: Filter by segment (user, agent, tool, guideline)
            content_query: Full-text search query
            page_size: Number of results per page (max 50)
            status: Filter by status (active, superseded, archived, all)

        Returns:
            Fern ListAgentMemoriesResponse (Pydantic) with .data and .meta.
            Serialized to dict by execute_tool_call() at the adapter boundary.
        """
        logger.debug(
            "Listing memories: subject_id=%s, scope=%s, system=%s",
            subject_id,
            scope,
            system,
        )
        kwargs: dict[str, Any] = {"page_size": page_size}
        optional_filters = {
            "subject_id": subject_id,
            "scope": scope,
            "system": system,
            "type": type,
            "segment": segment,
            "content_query": content_query,
            "status": status,
        }
        kwargs.update(
            {key: value for key, value in optional_filters.items() if value is not None}
        )
        response = await self.rest.agent_api_memories.list_agent_memories(
            **kwargs,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

        return response

    async def store_memory(
        self,
        content: str,
        system: str,
        type: str,
        segment: str,
        thought: str,
        scope: str = "subject",
        subject_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """
        Store a new memory entry.

        Args:
            content: The memory content
            system: Memory system tier (sensory, working, long_term)
            type: Memory type (iconic, echoic, haptic, episodic, semantic, procedural)
            segment: Logical segment (user, agent, tool, guideline)
            thought: Agent's reasoning for storing this memory
            scope: Visibility scope (subject, organization)
            subject_id: UUID of the subject (required for subject scope)
            metadata: Additional metadata (tags, references)

        Returns:
            Fern Memory model (Pydantic). Serialized to dict by
            execute_tool_call() at the adapter boundary.
        """
        from thenvoi.client.rest import MemoryCreateRequest

        logger.debug(
            "Storing memory: system=%s, type=%s, segment=%s, scope=%s",
            system,
            type,
            segment,
            scope,
        )
        memory_kwargs: dict[str, Any] = {
            "content": content,
            "system": system,
            "type": type,
            "segment": segment,
            "thought": thought,
            "scope": scope,
        }
        if subject_id is not None:
            memory_kwargs["subject_id"] = subject_id
        if metadata is not None:
            memory_kwargs["metadata"] = metadata
        response = await self.rest.agent_api_memories.create_agent_memory(
            memory=MemoryCreateRequest(**memory_kwargs),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        if not response.data:
            raise RuntimeError("Failed to store memory - no response data")
        return response.data

    async def get_memory(self, memory_id: str) -> Any:
        """
        Retrieve a specific memory by ID.

        Args:
            memory_id: Memory ID (UUID)

        Returns:
            Fern Memory model (Pydantic). Serialized to dict by
            execute_tool_call() at the adapter boundary.
        """
        logger.debug("Getting memory: id=%s", memory_id)
        response = await self.rest.agent_api_memories.get_agent_memory(
            id=memory_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        if not response.data:
            raise RuntimeError("Failed to get memory - no response data")
        return response.data

    async def supersede_memory(self, memory_id: str) -> Any:
        """
        Mark a memory as superseded (soft delete).

        Args:
            memory_id: Memory ID (UUID)

        Returns:
            Fern Memory model (Pydantic). Serialized to dict by
            execute_tool_call() at the adapter boundary.
        """
        logger.debug("Superseding memory: id=%s", memory_id)
        response = await self.rest.agent_api_memories.supersede_agent_memory(
            id=memory_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        if not response.data:
            raise RuntimeError("Failed to supersede memory - no response data")
        return response.data

    async def archive_memory(self, memory_id: str) -> Any:
        """
        Archive a memory (hide but preserve).

        Args:
            memory_id: Memory ID (UUID)

        Returns:
            Fern Memory model (Pydantic). Serialized to dict by
            execute_tool_call() at the adapter boundary.
        """
        logger.debug("Archiving memory: id=%s", memory_id)
        response = await self.rest.agent_api_memories.archive_agent_memory(
            id=memory_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        if not response.data:
            raise RuntimeError("Failed to archive memory - no response data")
        return response.data

    # --- Mention resolution ---

    def _resolve_mentions(
        self, mentions: list[str] | list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """
        Resolve mention handles, names, or IDs to {id, handle} dicts using cached participants.

        Lookup priority:
        1. Handle (unique identifier like @john or @john/agent-name)
        2. Name (display name, may not be unique)
        3. ID (UUID - for robustness when LLM passes IDs directly)

        Args:
            mentions: List of handles/names/IDs (strings) or already-resolved dicts

        Returns:
            List of {id, handle} dicts

        Raises:
            ValueError: If handle/name/ID is not found in participants
        """
        # Build lookup tables from cached participants
        # Strip @ prefix from handles for consistent matching (backend may or may not include @)
        handle_to_participant = {
            (p.get("handle") or "").lstrip("@"): p for p in self._participants
        }
        name_to_participant = {p.get("name"): p for p in self._participants}
        id_to_participant = {p.get("id"): p for p in self._participants}

        resolved = []
        for mention in mentions:
            if isinstance(mention, str):
                # Strip @ prefix if present (LLMs often include it)
                identifier = mention.lstrip("@")
            else:
                # Already-resolved dict with ID and handle
                if mention.get("id"):
                    resolved.append(
                        {"id": mention["id"], "handle": mention.get("handle", "")}
                    )
                    continue
                raw_identifier = mention.get("handle") or mention.get("name", "")
                identifier = raw_identifier.lstrip("@")

            # Try handle lookup first (handles are unique), then name, then ID
            participant = handle_to_participant.get(identifier)
            if not participant:
                participant = name_to_participant.get(identifier)
            if not participant:
                participant = id_to_participant.get(identifier)

            if not participant:
                available_handles = list(handle_to_participant.keys())
                raise ValueError(
                    f"Unknown participant '{identifier}'. "
                    f"Available handles: {available_handles}"
                )

            resolved.append(
                {"id": participant["id"], "handle": participant.get("handle", "")}
            )

        return resolved

    async def _lookup_peer(self, identifier: str) -> Any | None:
        """
        Find a peer by identifier (handle, name, or ID), paginating through all results.

        Args:
            identifier: Handle, name, or ID to search for (case-insensitive)

        Returns:
            Fern peer model if found, None otherwise
        """
        page = 1
        while True:
            result = await self.lookup_peers(page=page, page_size=100)
            peers = result.data or []
            for peer in peers:
                if _matches_identifier(peer, identifier):
                    return peer

            # Check if more pages
            metadata = result.metadata
            total_pages = metadata.total_pages if metadata else 1
            if page >= total_pages:
                break
            page += 1

        return None

    # --- Schema converters ---

    @property
    def tool_models(self) -> dict[str, type[BaseModel]]:
        """Get Pydantic models for all tools."""
        return TOOL_MODELS

    @property
    def is_hub_room(self) -> bool:
        """True if this AgentTools is bound to the contact hub room.

        When True, contact-management tool schemas are force-included by
        the schema methods regardless of the caller's include_contacts
        argument.
        """
        return self._hub_room_id is not None and self.room_id == self._hub_room_id

    def get_tool_schemas(
        self,
        format: str,
        *,
        include_memory: bool = False,
        include_contacts: bool = True,
    ) -> list[dict[str, Any]] | list["ToolParam"]:
        """
        Get tool schemas in provider-specific format.

        Args:
            format: Target format - "openai" or "anthropic"
            include_memory: If True, include memory tools (enterprise only)
            include_contacts: If True (default), include contact management
                tools. Adapters that gate contacts behind ``Capability.CONTACTS``
                should pass ``False`` when CONTACTS is not in features.
                When this AgentTools is bound to the hub room
                (``self.is_hub_room``), this argument is ignored and contact
                tools are always included.

        Returns:
            List of tool definitions in the requested format

        Raises:
            ValueError: If format is not "openai" or "anthropic"
        """
        if format not in ("openai", "anthropic"):
            raise ValueError(
                f"Invalid format: {format}. Must be 'openai' or 'anthropic'"
            )

        # HUB_ROOM auto-enable: force contact tools on for the hub-room
        # execution path. The hub-room prompt instructs the LLM to call
        # contact tools, so they must be exposed regardless of adapter
        # preference.
        effective_include_contacts = include_contacts or self.is_hub_room

        tools: list[Any] = []
        for definition in iter_tool_definitions(
            include_memory=include_memory,
            include_contacts=effective_include_contacts,
        ):
            schema = definition.input_model.model_json_schema()
            # Remove Pydantic-specific keys
            schema.pop("title", None)

            if format == "openai":
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": definition.name,
                            "description": definition.input_model.__doc__ or "",
                            "parameters": schema,
                        },
                    }
                )
            elif format == "anthropic":
                tools.append(
                    {
                        "name": definition.name,
                        "description": definition.input_model.__doc__ or "",
                        "input_schema": schema,
                    }
                )
        return tools

    def get_anthropic_tool_schemas(
        self,
        *,
        include_memory: bool = False,
        include_contacts: bool = True,
    ) -> list["ToolParam"]:
        """Get tool schemas in Anthropic format (strongly typed)."""
        return cast(
            list["ToolParam"],
            self.get_tool_schemas(
                "anthropic",
                include_memory=include_memory,
                include_contacts=include_contacts,
            ),
        )

    def get_openai_tool_schemas(
        self,
        *,
        include_memory: bool = False,
        include_contacts: bool = True,
    ) -> list[dict[str, Any]]:
        """Get tool schemas in OpenAI format (strongly typed)."""
        return cast(
            list[dict[str, Any]],
            self.get_tool_schemas(
                "openai",
                include_memory=include_memory,
                include_contacts=include_contacts,
            ),
        )

    async def execute_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """
        Execute a tool call by name with validated arguments.

        This is the single serialization boundary: individual tool methods
        may return Pydantic models (Fern-generated or otherwise), and this
        method converts them to dicts via .model_dump() so adapters always
        receive JSON-serializable results.

        ThenvoiToolError is re-raised so framework wrappers can translate it
        into framework-native failure results. Unexpected exceptions are
        caught and returned as error strings for the LLM.

        Args:
            tool_name: Name of the tool to execute
            arguments: Arguments to pass to the tool (validated against Pydantic model)

        Returns:
            Tool execution result (dict, string, or other JSON-serializable value),
            or error string if an unexpected failure occurred

        Raises:
            ThenvoiToolError: When a tool method raises a typed tool failure
        """
        # Validate arguments against Pydantic model
        try:
            definition = TOOL_DEFINITIONS.get(tool_name)
            if definition:
                arguments = validate_tool_arguments(
                    tool_name,
                    definition.input_model,
                    arguments,
                )
        except ValueError as error:
            return str(error)
        except Exception as e:
            return f"Error validating {tool_name} arguments: {e}"

        definition = TOOL_DEFINITIONS.get(tool_name)
        if definition is None:
            return f"Unknown tool: {tool_name}"

        try:
            method = getattr(self, definition.method_name)
            result = await method(**arguments)
            # Serialize Pydantic models to dicts at the adapter boundary
            if hasattr(result, "model_dump"):
                return result.model_dump()
            if isinstance(result, list):
                return [
                    item.model_dump() if hasattr(item, "model_dump") else item
                    for item in result
                ]
            return result
        except ThenvoiToolError:
            # Let ThenvoiToolError propagate so framework wrappers can
            # translate it into framework-native failure results.
            raise
        except Exception as e:
            return f"Error executing {tool_name}: {e}"


class HumanTools:
    """User-scoped tools for Thenvoi platform interaction.

    ``HumanTools`` is stateless per credential: one instance per user-scoped
    ``AsyncRestClient``. Unlike ``AgentTools`` it is not bound to a room —
    every chat/room-bound method takes its room identifier as a plain
    ``chat_id`` argument.

    Each method is a thin wrapper around a Fern ``human_api_*`` call. The
    observable tool surface mirrors today's ``thenvoi-mcp`` human tool
    handlers (Phase 1 of INT-338 copies those signatures verbatim); widening
    to full Fern parity is explicitly out of scope.
    """

    def __init__(self, rest: "AsyncRestClient") -> None:
        """Bind this HumanTools instance to a user-scoped REST client."""
        self.rest = rest

    # --- human_agents.py ---

    async def list_my_agents(
        self,
        page: int | None = None,
        page_size: int | None = None,
    ) -> Any:
        """List agents owned by the user."""
        logger.debug("Listing my agents: page=%s, page_size=%s", page, page_size)
        return await self.rest.human_api_agents.list_my_agents(
            page=page, page_size=page_size
        )

    async def register_my_agent(self, name: str, description: str) -> Any:
        """Register a new external agent owned by the user."""
        from thenvoi_rest import AgentRegisterRequest

        logger.debug("Registering my agent: name=%s", name)
        agent_request = AgentRegisterRequest(name=name, description=description)
        return await self.rest.human_api_agents.register_my_agent(
            agent=agent_request,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    # --- human_chats.py ---

    async def list_my_chats(
        self,
        page: int | None = None,
        page_size: int | None = None,
    ) -> Any:
        """List chat rooms where the user is a participant."""
        logger.debug("Listing my chats: page=%s, page_size=%s", page, page_size)
        return await self.rest.human_api_chats.list_my_chats(
            page=page, page_size=page_size
        )

    async def create_my_chat_room(self, task_id: str | None = None) -> Any:
        """Create a new chat room with the user as owner."""
        from thenvoi_rest import CreateMyChatRoomRequestChat

        logger.debug("Creating my chat room: task_id=%s", task_id)
        chat_request = (
            CreateMyChatRoomRequestChat(task_id=task_id)
            if task_id
            else CreateMyChatRoomRequestChat()
        )
        return await self.rest.human_api_chats.create_my_chat_room(
            chat=chat_request,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    async def get_my_chat_room(self, chat_id: str) -> Any:
        """Get a specific chat room by ID."""
        logger.debug("Getting my chat room: chat_id=%s", chat_id)
        return await self.rest.human_api_chats.get_my_chat_room(id=chat_id)

    # --- human_contacts.py ---

    async def list_my_contacts(
        self,
        page: int | None = None,
        page_size: int | None = None,
    ) -> Any:
        """List the user's active contacts."""
        logger.debug("Listing my contacts: page=%s, page_size=%s", page, page_size)
        return await self.rest.human_api_contacts.list_my_contacts(
            page=page, page_size=page_size
        )

    async def create_contact_request(
        self, recipient_handle: str, message: str | None = None
    ) -> Any:
        """Send a contact request to another user."""
        from thenvoi_rest import CreateContactRequestRequestContactRequest

        logger.debug("Creating contact request to: %s", recipient_handle)
        kwargs: dict[str, Any] = {"recipient_handle": recipient_handle}
        if message is not None:
            kwargs["message"] = message
        contact_request = CreateContactRequestRequestContactRequest(**kwargs)
        return await self.rest.human_api_contacts.create_contact_request(
            contact_request=contact_request,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    async def list_received_contact_requests(
        self,
        page: int | None = None,
        page_size: int | None = None,
    ) -> Any:
        """List contact requests received by the user (pending)."""
        logger.debug(
            "Listing received contact requests: page=%s, page_size=%s", page, page_size
        )
        return await self.rest.human_api_contacts.list_received_contact_requests(
            page=page, page_size=page_size
        )

    async def list_sent_contact_requests(
        self,
        status: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> Any:
        """List contact requests sent by the user."""
        logger.debug(
            "Listing sent contact requests: status=%s, page=%s, page_size=%s",
            status,
            page,
            page_size,
        )
        return await self.rest.human_api_contacts.list_sent_contact_requests(
            status=status, page=page, page_size=page_size
        )

    async def approve_contact_request(self, request_id: str) -> Any:
        """Approve a received contact request."""
        logger.debug("Approving contact request: %s", request_id)
        return await self.rest.human_api_contacts.approve_contact_request(
            id=request_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    async def reject_contact_request(self, request_id: str) -> Any:
        """Reject a received contact request."""
        logger.debug("Rejecting contact request: %s", request_id)
        return await self.rest.human_api_contacts.reject_contact_request(
            id=request_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    async def cancel_contact_request(self, request_id: str) -> Any:
        """Cancel a sent contact request."""
        logger.debug("Cancelling contact request: %s", request_id)
        return await self.rest.human_api_contacts.cancel_contact_request(
            id=request_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    async def resolve_handle(self, handle: str) -> Any:
        """Look up an entity by handle."""
        logger.debug("Resolving handle: %s", handle)
        return await self.rest.human_api_contacts.resolve_handle(handle=handle)

    async def remove_my_contact(
        self,
        contact_id: str | None = None,
        handle: str | None = None,
    ) -> Any:
        """Remove an existing contact by contact_id or handle.

        Returns an ``"Error: ..."`` string (matching today's MCP handler
        output verbatim) when neither ``contact_id`` nor ``handle`` is
        provided, so the observable tool-surface error shape is preserved.
        """
        if not contact_id and not handle:
            return "Error: Either contact_id or handle must be provided"

        logger.debug("Removing contact: contact_id=%s, handle=%s", contact_id, handle)
        # The Fern client uses OMIT for optional params; passing None sends
        # null. Build kwargs dynamically so we only send populated fields.
        kwargs: dict[str, Any] = {}
        if contact_id is not None:
            kwargs["contact_id"] = contact_id
        if handle is not None:
            kwargs["handle"] = handle
        return await self.rest.human_api_contacts.remove_my_contact(
            **kwargs,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    # --- human_messages.py ---

    async def list_my_chat_messages(
        self,
        chat_id: str,
        page: int | None = None,
        page_size: int | None = None,
        message_type: str | None = None,
        since: str | None = None,
    ) -> Any:
        """List messages in a chat room.

        ``since`` is an ISO 8601 timestamp string; the SDK converts it to a
        ``datetime`` before calling the Fern client. This mirrors today's
        MCP handler behavior.
        """
        logger.debug(
            "Listing chat messages: chat_id=%s, page=%s, page_size=%s",
            chat_id,
            page,
            page_size,
        )
        since_dt = None
        if since:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        return await self.rest.human_api_messages.list_my_chat_messages(
            chat_id=chat_id,
            page=page,
            page_size=page_size,
            message_type=message_type,
            since=since_dt,
        )

    async def send_my_chat_message(
        self,
        chat_id: str,
        content: str,
        recipients: str,
    ) -> Any:
        """Send a message in a chat room.

        ``recipients`` is a comma-separated list of participant names; the
        SDK resolves them against the chat participants. Empty input and
        unknown names return an ``"Error: ..."`` string matching today's
        MCP handler output verbatim (no exception raised) so the
        observable tool-surface error shape is preserved.
        """
        from thenvoi_rest import ChatMessageRequest, ChatMessageRequestMentionsItem

        recipient_names = [
            name.strip().lower() for name in recipients.split(",") if name.strip()
        ]
        if not recipient_names:
            return "Error: recipients cannot be empty"

        logger.debug(
            "Sending chat message: chat_id=%s, recipients=%s", chat_id, recipient_names
        )

        participants_response = (
            await self.rest.human_api_participants.list_my_chat_participants(
                chat_id=chat_id
            )
        )
        participants = participants_response.data or []

        name_to_participant: dict[str, Any] = {}
        for p in participants:
            if getattr(p, "name", None):
                name_to_participant[p.name.lower()] = p
            if getattr(p, "username", None):
                name_to_participant[p.username.lower()] = p
            if getattr(p, "first_name", None):
                name_to_participant[p.first_name.lower()] = p

        mentions_list: list[ChatMessageRequestMentionsItem] = []
        not_found: list[str] = []
        for name in recipient_names:
            participant = name_to_participant.get(name)
            if participant:
                display_name = getattr(participant, "name", None) or getattr(
                    participant, "username", "Unknown"
                )
                mentions_list.append(
                    ChatMessageRequestMentionsItem(id=participant.id, name=display_name)
                )
            else:
                not_found.append(name)

        if not_found:
            available = list(name_to_participant.keys())
            return (
                f"Error: Not found: {', '.join(not_found)}. "
                f"Available: {', '.join(available)}"
            )

        message_request = ChatMessageRequest(content=content, mentions=mentions_list)
        return await self.rest.human_api_messages.send_my_chat_message(
            chat_id=chat_id,
            message=message_request,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    # --- human_participants.py ---

    async def list_my_chat_participants(
        self,
        chat_id: str,
        participant_type: str | None = None,
    ) -> Any:
        """List participants in a chat room."""
        logger.debug(
            "Listing my chat participants: chat_id=%s, participant_type=%s",
            chat_id,
            participant_type,
        )
        return await self.rest.human_api_participants.list_my_chat_participants(
            chat_id=chat_id, participant_type=participant_type
        )

    async def add_my_chat_participant(
        self,
        chat_id: str,
        participant_id: str,
        role: str | None = None,
    ) -> str:
        """Add a participant to a chat room.

        Returns ``f"Added participant: {participant_id}"`` (discards the
        Fern response body) to match today's MCP handler output verbatim.
        """
        from thenvoi_rest import ParticipantRequest

        logger.debug(
            "Adding my chat participant: chat_id=%s, participant_id=%s, role=%s",
            chat_id,
            participant_id,
            role,
        )
        participant = ParticipantRequest(
            participant_id=participant_id, role=role or "member"
        )
        await self.rest.human_api_participants.add_my_chat_participant(
            chat_id=chat_id,
            participant=participant,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        return f"Added participant: {participant_id}"

    async def remove_my_chat_participant(
        self,
        chat_id: str,
        participant_id: str,
    ) -> str:
        """Remove a participant from a chat room.

        Returns ``f"Removed participant: {participant_id}"`` (discards the
        Fern response body) to match today's MCP handler output verbatim.
        """
        logger.debug(
            "Removing my chat participant: chat_id=%s, participant_id=%s",
            chat_id,
            participant_id,
        )
        await self.rest.human_api_participants.remove_my_chat_participant(
            chat_id=chat_id,
            id=participant_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        return f"Removed participant: {participant_id}"

    # --- human_memories.py ---

    async def list_user_memories(
        self,
        chat_room_id: str | None = None,
        scope: str | None = None,
        system: str | None = None,
        memory_type: str | None = None,
        segment: str | None = None,
        content_query: str | None = None,
        page_size: int | None = None,
        status: str | None = None,
    ) -> Any:
        """List memories available to the authenticated user."""
        logger.debug(
            "Listing user memories: chat_room_id=%s, scope=%s, system=%s",
            chat_room_id,
            scope,
            system,
        )
        return await self.rest.human_api_memories.list_user_memories(
            chat_room_id=chat_room_id,
            scope=scope,
            system=system,
            type=memory_type,
            segment=segment,
            content_query=content_query,
            page_size=page_size,
            status=status,
        )

    async def get_user_memory(self, memory_id: str) -> Any:
        """Get a single user memory by ID."""
        logger.debug("Getting user memory: memory_id=%s", memory_id)
        return await self.rest.human_api_memories.get_user_memory(memory_id)

    async def supersede_user_memory(self, memory_id: str) -> Any:
        """Mark a user memory as superseded."""
        logger.debug("Superseding user memory: memory_id=%s", memory_id)
        return await self.rest.human_api_memories.supersede_user_memory(
            memory_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    async def archive_user_memory(self, memory_id: str) -> Any:
        """Archive a user memory."""
        logger.debug("Archiving user memory: memory_id=%s", memory_id)
        return await self.rest.human_api_memories.archive_user_memory(
            memory_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    async def restore_user_memory(self, memory_id: str) -> Any:
        """Restore an archived user memory."""
        logger.debug("Restoring user memory: memory_id=%s", memory_id)
        return await self.rest.human_api_memories.restore_user_memory(
            memory_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    async def delete_user_memory(self, memory_id: str) -> dict[str, Any]:
        """Delete a user memory permanently.

        The Fern endpoint returns no body; we return a structured
        ``{"deleted": True, "id": memory_id}`` payload so the observable
        return shape matches today's MCP handler.
        """
        logger.debug("Deleting user memory: memory_id=%s", memory_id)
        await self.rest.human_api_memories.delete_user_memory(
            memory_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        return {"deleted": True, "id": memory_id}

    # --- human_profile.py / human_peers ---

    async def get_my_profile(self) -> Any:
        """Get the current user's profile details."""
        logger.debug("Getting my profile")
        return await self.rest.human_api_profile.get_my_profile()

    async def update_my_profile(
        self,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> Any:
        """Update the current user's profile.

        Returns an ``"Error: ..."`` string (matching today's MCP handler
        output verbatim) when neither field is provided, so the observable
        tool-surface error shape is preserved.
        """
        user_data: dict[str, Any] = {}
        if first_name is not None:
            user_data["first_name"] = first_name
        if last_name is not None:
            user_data["last_name"] = last_name
        if not user_data:
            return (
                "Error: At least one field (first_name or last_name) must be provided"
            )

        logger.debug("Updating my profile: fields=%s", list(user_data.keys()))
        return await self.rest.human_api_profile.update_my_profile(
            user=cast(Any, user_data),
            request_options=DEFAULT_REQUEST_OPTIONS,
        )

    async def list_my_peers(
        self,
        not_in_chat: str | None = None,
        peer_type: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> Any:
        """List entities the user can interact with in chat rooms."""
        logger.debug(
            "Listing my peers: not_in_chat=%s, peer_type=%s, page=%s, page_size=%s",
            not_in_chat,
            peer_type,
            page,
            page_size,
        )
        return await self.rest.human_api_peers.list_my_peers(
            not_in_chat=not_in_chat,
            type=peer_type,
            page=page,
            page_size=page_size,
        )
