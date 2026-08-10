"""
AgentTools - Tools for LLM platform interaction.

Bound to a room_id. Uses AsyncRestClient directly for API calls.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import warnings
from dataclasses import dataclass
from datetime import datetime
from collections.abc import AsyncIterator, Awaitable, Callable, Collection
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from pydantic import (
    AliasChoices,
    BaseModel,
    Field,
    ValidationError,
    create_model,
    model_validator,
)

from band.client.rest import ChatRoomRequest, DEFAULT_REQUEST_OPTIONS
from band.runtime.participants import participant_snapshot
from band.core.exceptions import BandToolError
from band.core.memory_types import (
    MemoryListScope,
    MemorySegment,
    MemoryStatus,
    MemoryStoreScope,
    MemorySystem,
    MemoryType,
    memory_type_field_description,
    validate_memory_type_for_system,
    validate_subject_scope,
)
from band.core.protocols import AgentToolsProtocol
from band.core.tool_filter import sanitize_tool_schema
from band.core.types import ContactRequestSentStatus, EventMessageType

if TYPE_CHECKING:
    from anthropic.types import ToolParam

    from band.client.rest import (
        AsyncRestClient,
        ListAgentContactRequestsResponse,
        ListAgentContactsResponse,
        ListAgentMemoriesResponse,
        ListAgentPeersResponse,
    )

    from .execution import ExecutionContext

logger = logging.getLogger(__name__)

CHAT_PAGE_SIZE = 100
# The walk below stops on the server's own page count, so it is capped too:
# 5,000 rooms is far past any real agent, and a listing that never reports a
# final page then degrades to a bounded read instead of looping forever.
MAX_CHAT_PAGES = 50


async def iter_chat_pages(
    fetch: Callable[[int, int], Awaitable[Any]],
) -> AsyncIterator[Any]:
    """Yield each page of a chat listing, oldest page first."""
    for page in range(1, MAX_CHAT_PAGES + 1):
        response = await fetch(page, CHAT_PAGE_SIZE)
        yield response
        total_pages = getattr(response.metadata, "total_pages", None)
        if not total_pages or page >= int(total_pages):
            return
    logger.warning(
        "Stopped listing chats at the %d page cap; some rooms were not read",
        MAX_CHAT_PAGES,
    )


# The Agent Events API enforces a hard cap on event content (see
# thenvoi-platform's events_controller.ex `@content_max_length`) and rejects
# anything larger with a 422 before it ever reaches the room; it also rejects
# a blank string outright ("content can't be blank"). Event content can be
# arbitrarily large or entirely absent in practice — e.g. an ACP tool_result
# mirroring a large file, or a tool call whose result has no text
# representation (a terminal- or diff-only ACP tool_call_update) — so guard
# both ends defensively rather than letting the send fail.
_EVENT_CONTENT_MAX_LENGTH = 16384
_EVENT_TRUNCATION_MARKER = "... [truncated] ..."
_EVENT_EMPTY_CONTENT_PLACEHOLDER = "(no content)"


def _truncate_event_content(content: str) -> str:
    """Cap *content* at ``_EVENT_CONTENT_MAX_LENGTH`` chars, keeping its head
    and tail around a marker.

    Both ends are preserved because the tail is often the informative part of a
    truncated payload — the final lines of a raw error dump, or a trailing
    status — which a head-only cut would silently drop. A no-op when *content*
    is already within the limit, so callers can run it unconditionally rather
    than checking the length themselves first.
    """
    if len(content) <= _EVENT_CONTENT_MAX_LENGTH:
        return content
    budget = _EVENT_CONTENT_MAX_LENGTH - len(_EVENT_TRUNCATION_MARKER)
    head_len = budget // 2
    tail_len = budget - head_len
    return content[:head_len] + _EVENT_TRUNCATION_MARKER + content[-tail_len:]


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


def available_mention_handles(
    participants: list[dict[str, Any] | Any],
    agent_id: str | None = None,
) -> list[str]:
    """Return room handles this agent may mention, excluding itself."""
    return [
        handle
        for participant in participants
        if (handle := _entity_field(participant, "handle"))
        and (agent_id is None or _entity_field(participant, "id") != agent_id)
    ]


# Single marker for the available-handles hint. Used both to render the hint and
# to detect it, so the producer and the idempotency guard can never drift apart.
_AVAILABLE_HANDLES_MARKER = "Available handles:"


def append_mention_handles_hint(error: str, handles: list[str]) -> str:
    """Append a retryable handles hint to a tool error when handles are known.

    Idempotent: an error that already carries the hint is returned unchanged, so
    the same error can flow through multiple adapter enrichers without doubling
    the handle list.
    """
    if not handles or _AVAILABLE_HANDLES_MARKER in error:
        return error
    return (
        f"{error}. {_AVAILABLE_HANDLES_MARKER} {handles}. "
        "Use participant handles from the list."
    )


def append_available_mention_handles(
    error: str,
    participants: list[dict[str, Any] | Any],
    agent_id: str | None = None,
) -> str:
    """Append retryable mention handles to a tool error when available."""
    return append_mention_handles_hint(
        error, available_mention_handles(participants, agent_id)
    )


@dataclass(frozen=True)
class ToolDefinition:
    """Metadata for a built-in Band tool."""

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
    message_type: EventMessageType = Field(..., description="Type of event")
    metadata: dict[str, Any] | None = Field(
        None, description="Optional structured data for the event"
    )


class ListRoomFilesInput(BaseModel):
    """List files recently shared in this chat room, newest first.

    Use this when someone mentions a file ("this file", "the file I sent")
    to find its file_id before reading it with band_read_room_file. You only
    see files from messages that @mentioned you — if a file is missing, ask
    the sender to @mention you with it.
    """


class ReadRoomFileInput(BaseModel):
    """Read a file shared in this chat room.

    Text files return their content. Images are shown to you directly — you
    can see them. Other binary formats return a description of the file.
    """

    file_id: str = Field(
        ...,
        description="The file id, from band_list_room_files or a message's attachments",
    )


class SendRoomFileInput(BaseModel):
    """Create a small text file and share it in this chat room.

    The file is uploaded and attached to a new message from you. Messages
    require a mention, so pass the handle of whoever you're replying to.
    Prefer this over pasting a wall of text when sharing something you wrote
    (a plan, a list, a report).
    """

    filename: str = Field(..., description="Name for the file, e.g. plan.txt")
    text_content: str = Field(..., description="The file's text content")
    mention: str = Field(
        ...,
        description=(
            "Handle of the participant to address. For users: @<username>. "
            "For agents: @<username>/<agent-name>."
        ),
    )
    message: str = Field("", description="Optional message text to accompany the file")


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
    sent_status: ContactRequestSentStatus = Field(
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
    scope: MemoryListScope | None = Field(None, description="Filter by scope")
    system: MemorySystem | None = Field(None, description="Filter by memory system")
    type: MemoryType | None = Field(None, description="Filter by memory type")
    segment: MemorySegment | None = Field(None, description="Filter by segment")
    content_query: str | None = Field(None, description="Full-text search query")
    page_size: int = Field(50, description="Number of results per page", ge=1, le=50)
    status: MemoryStatus | None = Field(None, description="Filter by status")


class StoreMemoryInput(BaseModel):
    """Store a new memory entry.

    The memory will be associated with the authenticated agent as the source.
    For subject-scoped memories, provide a subject_id.
    For organization-scoped memories, omit subject_id.
    """

    content: str = Field(..., description="The memory content")
    system: MemorySystem = Field(..., description="Memory system tier")
    type: MemoryType = Field(..., description=memory_type_field_description())
    segment: MemorySegment = Field(..., description="Logical segment")
    thought: str = Field(..., description="Agent's reasoning for storing this memory")
    scope: MemoryStoreScope = Field(..., description="Visibility scope")
    subject_id: str | None = Field(
        None,
        description="UUID of the subject this memory is about (required for subject scope)",
    )
    metadata: dict[str, Any] | None = Field(
        None, description="Additional metadata (tags, references)"
    )

    @model_validator(mode="after")
    def validate_memory_fields(self) -> "StoreMemoryInput":
        validate_memory_type_for_system(self.system, self.type)
        validate_subject_scope(self.scope, self.subject_id)
        return self


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


# --- Human-tool input models (copied from band-mcp/src/band_mcp/tools/human/*.py) ---
#
# These models mirror the current band-mcp human tool handler signatures
# field-for-field. They are the canonical contract preserved by Phase 1 of
# INT-338: the observable tool surface stays identical to today's MCP
# behavior. Widening to full Fern parity is out of scope for this ticket.


# human_agents.py


class ListMyAgentsInput(BaseModel):
    """List agents owned by the user."""

    page: int | None = Field(None, description="Page number (optional).")
    page_size: int | None = Field(None, description="Items per page (optional).")


class RegisterMyAgentInput(BaseModel):
    """Register a new remote agent.

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


# The name the Band MCP server registers under. MCP clients key tool
# namespacing off it (e.g. Copilot's hyphen-joined ``band-<tool>``), and
# adapters reference it when advertising the server in a session config.
# The one source of truth: ``_resolve_mcp_tool_name`` anchors its prefix
# match here, and ``integrations.mcp.backends`` names the server from it.
BAND_MCP_SERVER_NAME = "band"

# Tool names whose successful call posts a visible message into the room.
# Bridge adapters (copilot_sdk, codex, ACP client) use this to suppress their
# fallback text relay once the turn has already replied in the room, so the
# reply is delivered exactly once. band-mcp 1.3.2+ advertises the SDK-native
# ``band_send_message`` (its registrar reuses these SDK tool definitions), which
# the ``<server>-`` prefix match already covers; ``create_agent_chat_message``
# is the legacy band-mcp <=1.3.1 spelling, kept so older out-of-process servers
# still match.
ROOM_POSTING_TOOL_NAMES: frozenset[str] = frozenset(
    {"band_send_message", "create_agent_chat_message", "band_send_room_file"}
)


def _resolve_mcp_tool_name(tool_name: str, names: Collection[str]) -> str | None:
    """The member of ``names`` behind ``tool_name``'s MCP spelling, if any.

    The one resolver for the one MCP naming convention seen in practice: the
    Band loopback server's own hyphen-joined ``band-<tool>`` prefix (e.g.
    Copilot CLI surfaces ``band_send_message`` as ``band-band_send_message``;
    band-mcp <=1.3.1's legacy spelling arrives as
    ``band-create_agent_chat_message``). Anchored to ``BAND_MCP_SERVER_NAME``
    specifically -- not any prefix before a hyphen -- so an unrelated MCP
    server's own tool (e.g. ``other-band_send_message``) never resolves as a
    Band tool. Other spellings (``mcp__server__tool``, ``server.tool``) are
    not matched either -- no wired backend uses them. Extend here when such a
    backend is added.
    """
    if tool_name in names:
        return tool_name
    prefix = f"{BAND_MCP_SERVER_NAME}-"
    suffix = tool_name.removeprefix(prefix)
    return suffix if suffix != tool_name and suffix in names else None


def is_room_posting_tool(tool_name: str) -> bool:
    """True when a successful call of ``tool_name`` posts a message to the room.

    Tolerates the Band MCP server's own ``band-`` spelling (see
    ``_resolve_mcp_tool_name``) but nothing else -- an unrelated MCP server's
    tool that merely ends in ``-band_send_message`` (e.g.
    ``other-band_send_message``) never resolves as room-posting, since its
    prefix isn't ``band``. A miss only costs a duplicate reply (the
    pre-suppression behavior), never a wrong post.
    """
    return _resolve_mcp_tool_name(tool_name, ROOM_POSTING_TOOL_NAMES) is not None


def canonicalize_mcp_tool_name(tool_name: str, own_names: Collection[str]) -> str:
    """The canonical band tool name behind the Band MCP server's ``band-`` spelling.

    Narrated ``tool_call``/``tool_result`` events must carry the canonical
    name like every other adapter's, so consumers match on one vocabulary.
    A name that doesn't reveal one of ``own_names`` behind ``band-`` passes
    through untouched -- including another MCP server's own tool.
    """
    return _resolve_mcp_tool_name(tool_name, own_names) or tool_name


# Registry mapping tool names to their schemas and bound AgentTools methods.
TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "band_send_message": ToolDefinition(
        name="band_send_message",
        input_model=SendMessageInput,
        method_name="send_message",
    ),
    "band_send_event": ToolDefinition(
        name="band_send_event",
        input_model=SendEventInput,
        method_name="send_event",
    ),
    "band_list_room_files": ToolDefinition(
        name="band_list_room_files",
        input_model=ListRoomFilesInput,
        method_name="list_room_files",
    ),
    "band_read_room_file": ToolDefinition(
        name="band_read_room_file",
        input_model=ReadRoomFileInput,
        method_name="read_room_file",
    ),
    "band_send_room_file": ToolDefinition(
        name="band_send_room_file",
        input_model=SendRoomFileInput,
        method_name="send_room_file",
    ),
    "band_add_participant": ToolDefinition(
        name="band_add_participant",
        input_model=AddParticipantInput,
        method_name="add_participant",
    ),
    "band_remove_participant": ToolDefinition(
        name="band_remove_participant",
        input_model=RemoveParticipantInput,
        method_name="remove_participant",
    ),
    "band_lookup_peers": ToolDefinition(
        name="band_lookup_peers",
        input_model=LookupPeersInput,
        method_name="lookup_peers",
    ),
    "band_get_participants": ToolDefinition(
        name="band_get_participants",
        input_model=GetParticipantsInput,
        method_name="get_participants",
    ),
    "band_create_chatroom": ToolDefinition(
        name="band_create_chatroom",
        input_model=CreateChatroomInput,
        method_name="create_chatroom",
    ),
    "band_list_contacts": ToolDefinition(
        name="band_list_contacts",
        input_model=ListContactsInput,
        method_name="list_contacts",
    ),
    "band_add_contact": ToolDefinition(
        name="band_add_contact",
        input_model=AddContactInput,
        method_name="add_contact",
    ),
    "band_remove_contact": ToolDefinition(
        name="band_remove_contact",
        input_model=RemoveContactInput,
        method_name="remove_contact",
    ),
    "band_list_contact_requests": ToolDefinition(
        name="band_list_contact_requests",
        input_model=ListContactRequestsInput,
        method_name="list_contact_requests",
    ),
    "band_respond_contact_request": ToolDefinition(
        name="band_respond_contact_request",
        input_model=RespondContactRequestInput,
        method_name="respond_contact_request",
    ),
    "band_list_memories": ToolDefinition(
        name="band_list_memories",
        input_model=ListMemoriesInput,
        method_name="list_memories",
    ),
    "band_store_memory": ToolDefinition(
        name="band_store_memory",
        input_model=StoreMemoryInput,
        method_name="store_memory",
    ),
    "band_get_memory": ToolDefinition(
        name="band_get_memory",
        input_model=GetMemoryInput,
        method_name="get_memory",
    ),
    "band_supersede_memory": ToolDefinition(
        name="band_supersede_memory",
        input_model=SupersedeMemoryInput,
        method_name="supersede_memory",
    ),
    "band_archive_memory": ToolDefinition(
        name="band_archive_memory",
        input_model=ArchiveMemoryInput,
        method_name="archive_memory",
    ),
    # --- Human tools (surface="human") ---
    # One entry per method in the Phase 1 human-tool mapping table.
    # Method names match HumanTools attributes; hasattr(HumanTools, method_name)
    # must resolve for every surface="human" definition.
    "band_list_my_agents": ToolDefinition(
        name="band_list_my_agents",
        input_model=ListMyAgentsInput,
        method_name="list_my_agents",
        surface="human",
    ),
    "band_register_my_agent": ToolDefinition(
        name="band_register_my_agent",
        input_model=RegisterMyAgentInput,
        method_name="register_my_agent",
        surface="human",
    ),
    "band_list_my_chats": ToolDefinition(
        name="band_list_my_chats",
        input_model=ListMyChatsInput,
        method_name="list_my_chats",
        surface="human",
    ),
    "band_create_my_chat_room": ToolDefinition(
        name="band_create_my_chat_room",
        input_model=CreateMyChatRoomInput,
        method_name="create_my_chat_room",
        surface="human",
    ),
    "band_get_my_chat_room": ToolDefinition(
        name="band_get_my_chat_room",
        input_model=GetMyChatRoomInput,
        method_name="get_my_chat_room",
        surface="human",
    ),
    "band_list_my_contacts": ToolDefinition(
        name="band_list_my_contacts",
        input_model=ListMyContactsInput,
        method_name="list_my_contacts",
        surface="human",
    ),
    "band_create_contact_request": ToolDefinition(
        name="band_create_contact_request",
        input_model=CreateContactRequestInput,
        method_name="create_contact_request",
        surface="human",
    ),
    "band_list_received_contact_requests": ToolDefinition(
        name="band_list_received_contact_requests",
        input_model=ListReceivedContactRequestsInput,
        method_name="list_received_contact_requests",
        surface="human",
    ),
    "band_list_sent_contact_requests": ToolDefinition(
        name="band_list_sent_contact_requests",
        input_model=ListSentContactRequestsInput,
        method_name="list_sent_contact_requests",
        surface="human",
    ),
    "band_approve_contact_request": ToolDefinition(
        name="band_approve_contact_request",
        input_model=ApproveContactRequestInput,
        method_name="approve_contact_request",
        surface="human",
    ),
    "band_reject_contact_request": ToolDefinition(
        name="band_reject_contact_request",
        input_model=RejectContactRequestInput,
        method_name="reject_contact_request",
        surface="human",
    ),
    "band_cancel_contact_request": ToolDefinition(
        name="band_cancel_contact_request",
        input_model=CancelContactRequestInput,
        method_name="cancel_contact_request",
        surface="human",
    ),
    "band_resolve_handle": ToolDefinition(
        name="band_resolve_handle",
        input_model=ResolveHandleInput,
        method_name="resolve_handle",
        surface="human",
    ),
    "band_remove_my_contact": ToolDefinition(
        name="band_remove_my_contact",
        input_model=RemoveMyContactInput,
        method_name="remove_my_contact",
        surface="human",
    ),
    "band_list_my_chat_messages": ToolDefinition(
        name="band_list_my_chat_messages",
        input_model=ListMyChatMessagesInput,
        method_name="list_my_chat_messages",
        surface="human",
    ),
    "band_send_my_chat_message": ToolDefinition(
        name="band_send_my_chat_message",
        input_model=SendMyChatMessageInput,
        method_name="send_my_chat_message",
        surface="human",
    ),
    "band_list_my_chat_participants": ToolDefinition(
        name="band_list_my_chat_participants",
        input_model=ListMyChatParticipantsInput,
        method_name="list_my_chat_participants",
        surface="human",
    ),
    "band_add_my_chat_participant": ToolDefinition(
        name="band_add_my_chat_participant",
        input_model=AddMyChatParticipantInput,
        method_name="add_my_chat_participant",
        surface="human",
    ),
    "band_remove_my_chat_participant": ToolDefinition(
        name="band_remove_my_chat_participant",
        input_model=RemoveMyChatParticipantInput,
        method_name="remove_my_chat_participant",
        surface="human",
    ),
    "band_list_user_memories": ToolDefinition(
        name="band_list_user_memories",
        input_model=ListUserMemoriesInput,
        method_name="list_user_memories",
        surface="human",
    ),
    "band_get_user_memory": ToolDefinition(
        name="band_get_user_memory",
        input_model=GetUserMemoryInput,
        method_name="get_user_memory",
        surface="human",
    ),
    "band_supersede_user_memory": ToolDefinition(
        name="band_supersede_user_memory",
        input_model=SupersedeUserMemoryInput,
        method_name="supersede_user_memory",
        surface="human",
    ),
    "band_archive_user_memory": ToolDefinition(
        name="band_archive_user_memory",
        input_model=ArchiveUserMemoryInput,
        method_name="archive_user_memory",
        surface="human",
    ),
    "band_restore_user_memory": ToolDefinition(
        name="band_restore_user_memory",
        input_model=RestoreUserMemoryInput,
        method_name="restore_user_memory",
        surface="human",
    ),
    "band_delete_user_memory": ToolDefinition(
        name="band_delete_user_memory",
        input_model=DeleteUserMemoryInput,
        method_name="delete_user_memory",
        surface="human",
    ),
    "band_get_my_profile": ToolDefinition(
        name="band_get_my_profile",
        input_model=GetMyProfileInput,
        method_name="get_my_profile",
        surface="human",
    ),
    "band_update_my_profile": ToolDefinition(
        name="band_update_my_profile",
        input_model=UpdateMyProfileInput,
        method_name="update_my_profile",
        surface="human",
    ),
    "band_list_my_peers": ToolDefinition(
        name="band_list_my_peers",
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
        "band_list_memories",
        "band_store_memory",
        "band_get_memory",
        "band_supersede_memory",
        "band_archive_memory",
    }
)

# Contact tools - explicitly listed (not derived by heuristic) because a
# future tool whose name happens to contain "contact" (e.g.
# band_get_contact_context) would be silently misclassified.
CONTACT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "band_list_contacts",
        "band_add_contact",
        "band_remove_contact",
        "band_list_contact_requests",
        "band_respond_contact_request",
    }
)

# File tools - explicitly listed for the same reason as contacts: gating by
# name must never depend on a substring heuristic.
FILE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "band_list_room_files",
        "band_read_room_file",
        "band_send_room_file",
    }
)

# Inline cap for text files read into a turn: enough for notes and log
# excerpts, small enough that one file can't blow out the context window.
_FILE_TEXT_INLINE_LIMIT = 16_384

# Images up to this size are handed to the model as vision input; larger
# ones get described instead. Vision cost scales with pixels, and anything a
# chat participant drags in sits comfortably under this.
_FILE_IMAGE_INLINE_LIMIT = 3 * 1024 * 1024

_FILE_TEXTLIKE_PREFIXES = ("text/", "application/json", "application/xml")


def _filename_from_disposition(header: str | None) -> str | None:
    """Extract the filename from a Content-Disposition header, if any."""
    if not header:
        return None
    match = re.search(r'filename\*?=(?:"([^"]+)"|([^;]+))', header)
    if not match:
        return None
    return (match.group(1) or match.group(2)).strip()


# Read-only / informational agent tools - explicitly listed (not derived by a
# name heuristic) because misclassifying a write tool as read-only would weaken
# the benign-empty-answer suppression in the crewai/pydantic-ai adapters. These
# tools only *fetch* state; running one is not a terminal action and does not
# constitute a reply, so a turn that runs only these and then yields an empty
# final answer is a genuine no-response failure, not benign noise.
READ_ONLY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "band_get_participants",
        "band_lookup_peers",
        "band_list_contacts",
        "band_list_contact_requests",
        "band_list_memories",
        "band_get_memory",
        "band_list_room_files",
        "band_read_room_file",
    }
)

# Event-emitting tools are observational, not terminal work: band_send_event posts a
# thought/error/task event (narration/status) — not a chat reply or a durable requested
# action. Like read-only tools, a turn that only sends an event and then yields an empty
# final answer is a genuine no-response failure, not benign (see is_terminal_success).
EVENT_TOOL_NAMES: frozenset[str] = frozenset({"band_send_event"})

# Human-surface memory tools - parallel to MEMORY_TOOL_NAMES but on the
# ``surface="human"`` side of the registry. Used by iter_tool_definitions()
# to apply the ``include_memory`` filter uniformly across both surfaces.
HUMAN_MEMORY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "band_list_user_memories",
        "band_get_user_memory",
        "band_supersede_user_memory",
        "band_archive_user_memory",
        "band_restore_user_memory",
        "band_delete_user_memory",
    }
)

# Human-surface contact tools - parallel to CONTACT_TOOL_NAMES.
HUMAN_CONTACT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "band_list_my_contacts",
        "band_create_contact_request",
        "band_list_received_contact_requests",
        "band_list_sent_contact_requests",
        "band_approve_contact_request",
        "band_reject_contact_request",
        "band_cancel_contact_request",
        "band_resolve_handle",
        "band_remove_my_contact",
    }
)

# Derived from TOOL_MODELS — single source of truth
ALL_TOOL_NAMES: frozenset[str] = frozenset(TOOL_MODELS.keys())


def band_tool_errored(tool_name: str | None, content: Any) -> bool:
    """Whether a Band tool call failed, by its wrapper's error convention.

    Band tool wrappers catch exceptions and return a string starting with
    ``"Error "``. Only known Band tools follow this convention (custom tools do not),
    so it is checked for ``ALL_TOOL_NAMES`` members only. (crewai detects failure
    differently — via its JSON ``status`` envelope — and does not use this helper.)
    """
    return (
        tool_name in ALL_TOOL_NAMES
        and isinstance(content, str)
        and content.startswith("Error ")
    )


def is_terminal_success(
    tool_name: str | None,
    *,
    succeeded: bool,
    custom_terminal: bool = False,
) -> bool:
    """Whether a finished tool call counts as terminal productive work.

    Single source of truth shared by the crewai / pydantic-ai adapters to decide
    whether an empty final model response is *benign* (the agent already did its
    work this turn) or a genuine no-response failure. Terminal work is:

    * a Band tool that is not read-only, not observational, and did not fail, or
    * a custom tool the caller declares terminal (``custom_terminal=True``).

    Read-only Band tools (``READ_ONLY_TOOL_NAMES``) never count — fetching state is
    not a terminal action. Observational tools (``EVENT_TOOL_NAMES`` — band_send_event
    posts a thought/error/task event) don't count either: emitting narration/status is
    not a chat reply or a durable requested action. Custom tools are **not** terminal
    by default: the SDK cannot know whether a bare custom tool is a lookup or a
    side-effecting action, so it fails loud — an empty final after only an undeclared
    custom tool surfaces as a no-response error rather than being silently swallowed.
    A custom tool that genuinely completes the turn opts in (see
    ``runtime.custom_tools.is_marked_terminal``).
    """
    if not succeeded:
        return False
    if tool_name in READ_ONLY_TOOL_NAMES or tool_name in EVENT_TOOL_NAMES:
        return False
    if tool_name in ALL_TOOL_NAMES:
        return True
    return custom_terminal


def missing_reply_error(framework: str, *, detail: str = "") -> str:
    """The room-visible error for a turn that ended without a reply going out.

    Raised by every adapter that answers through tools, so the wording lives
    once. Both endings are named because they look identical from the room and
    are told apart only by the model's last response: a plain-text final answer
    the adapter cannot post, or no output at all (empty or thinking-only), which
    is what a model that considers the exchange finished actually returns.
    """
    reasons = (
        f"{framework} finished a turn without calling band_send_message, so "
        "nothing reached the room. The model either answered in plain text "
        "instead of using the tool, or returned no output at all."
    )
    return f"{reasons} {detail}" if detail else reasons


# Fail fast on typos — catch at import time, not in a test run.
# Use explicit checks instead of ``assert`` so they are not stripped by -O.
if MEMORY_TOOL_NAMES - ALL_TOOL_NAMES:
    raise ValueError(f"Unknown memory tools: {MEMORY_TOOL_NAMES - ALL_TOOL_NAMES}")
if CONTACT_TOOL_NAMES - ALL_TOOL_NAMES:
    raise ValueError(f"Unknown contact tools: {CONTACT_TOOL_NAMES - ALL_TOOL_NAMES}")
if READ_ONLY_TOOL_NAMES - ALL_TOOL_NAMES:
    raise ValueError(
        f"Unknown read-only tools: {READ_ONLY_TOOL_NAMES - ALL_TOOL_NAMES}"
    )
if EVENT_TOOL_NAMES - ALL_TOOL_NAMES:
    raise ValueError(f"Unknown event tools: {EVENT_TOOL_NAMES - ALL_TOOL_NAMES}")

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

BASE_TOOL_NAMES: frozenset[str] = ALL_TOOL_NAMES - MEMORY_TOOL_NAMES - FILE_TOOL_NAMES
CHAT_TOOL_NAMES: frozenset[str] = BASE_TOOL_NAMES - CONTACT_TOOL_NAMES
MCP_TOOL_PREFIX: str = "mcp__band__"

# AdapterFeatures category for each platform tool name. Shared across adapters
# so include_categories filtering is consistent (chat/contacts/memory/files).
_TOOL_CATEGORIES: dict[str, str] = {
    **{name: "chat" for name in CHAT_TOOL_NAMES},
    **{name: "contacts" for name in CONTACT_TOOL_NAMES},
    **{name: "memory" for name in MEMORY_TOOL_NAMES},
    **{name: "files" for name in FILE_TOOL_NAMES},
}


def get_band_tool_category(name: str) -> str | None:
    """Return the AdapterFeatures category ("chat"/"contacts"/"memory"/"files") for a tool."""
    return _TOOL_CATEGORIES.get(name)


def mcp_tool_names(names: frozenset[str]) -> list[str]:
    """Convert base tool names to MCP-prefixed names for Claude SDK.

    Returns a sorted list for deterministic ordering across runs.
    """
    return [f"{MCP_TOOL_PREFIX}{name}" for name in sorted(names)]


def resolve_tool_model(name: str) -> type[BaseModel] | None:
    """Resolve a tool name to its master input model.

    Accepts the deprecated unprefixed spelling too, so every consumer sees one
    resolution rule. Warning about the deprecated form is the caller's job —
    this stays quiet so it can be used for lookups that aren't user-facing.
    """
    return TOOL_MODELS.get(name) or TOOL_MODELS.get(f"band_{name}")


def get_tool_description(name: str) -> str:
    """
    Get the LLM-optimized description for a tool.

    Use this to get consistent tool descriptions across all adapters.
    Descriptions are sourced from the Pydantic model docstrings.

    Args:
        name: Tool name (e.g., "band_send_message", "band_lookup_peers")
              Also accepts unprefixed names for backwards compatibility (deprecated).

    Returns:
        Tool description string
    """
    model = resolve_tool_model(name)
    if model is None or not model.__doc__:
        return f"Execute {name}"

    if name not in TOOL_MODELS:
        warnings.warn(
            f"Tool name '{name}' is deprecated. Use 'band_{name}' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
    return model.__doc__


def format_arg_doc(name: str, description: str) -> str:
    """Render one Google-style ``Args:`` entry.

    Continuation lines are indented past the argument name so a multi-line
    ``Field(description=...)`` stays part of that argument — flush-left
    continuations end the entry as far as a docstring parser is concerned.
    """
    head, *rest = description.strip().splitlines()
    return "\n".join(
        [f"    {name}: {head}", *(f"        {line.strip()}" for line in rest)]
    )


def get_tool_docstring_with_args(name: str) -> str:
    """Return the tool description plus a Google-style ``Args:`` section.

    Both halves come from the master model: the class docstring and each
    field's ``Field(description=...)``. Frameworks that build their schema by
    parsing a Python function's docstring (pydantic-ai via griffe) only see
    per-argument text if it appears under ``Args:``, so this renders it there
    rather than having each adapter retype it.
    """
    description = get_tool_description(name)
    model = resolve_tool_model(name)
    if model is None:
        return description

    arg_lines = [
        format_arg_doc(field_name, field.description)
        for field_name, field in model.model_fields.items()
        if field.description and field.description.strip()
    ]
    if not arg_lines:
        return description
    return f"{description.rstrip()}\n\nArgs:\n" + "\n".join(arg_lines)


ToolFunc = TypeVar("ToolFunc", bound=Callable[..., Any])


def platform_tool(fn: ToolFunc) -> ToolFunc:
    """Give a tool function the master description and ``Args:`` section.

    For frameworks that derive their schema from the function docstring. Reads
    ``fn.__name__`` rather than taking a tool name argument — the function is
    always named after the tool it registers (frameworks that key a tool by
    its function name, like pydantic-ai, require this already), so there is
    nowhere left to retype that name, let alone the description.
    """
    fn.__doc__ = get_tool_docstring_with_args(fn.__name__)
    return fn


def platform_args_schema(
    name: str,
    *,
    validators: dict[str, Any] | None = None,
) -> type[BaseModel]:
    """Return the master input model for ``name`` as a framework args schema.

    ``validators`` layers extra pydantic validators onto a subclass for
    frameworks whose tool-calling layer emits a value the master model is too
    strict to parse. There is deliberately no field or description override:
    an adapter needing different *text* has a modeling problem to fix on the
    master, not a formatting one to patch locally.
    """
    model = resolve_tool_model(name)
    if model is None:
        raise KeyError(name)
    if not validators:
        return model
    return create_model(
        f"{model.__name__}Adapted",
        __base__=model,
        # create_model does not inherit the base docstring, and that docstring
        # is the tool description every adapter reads.
        __doc__=model.__doc__,
        __validators__=validators,
    )


def iter_tool_definitions(
    *,
    surface: Literal["agent", "human"] | None = "agent",
    include_memory: bool = False,
    include_contacts: bool = True,
    include_files: bool = False,
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
      memory tools (``band_list_user_memories``, etc.).
    - ``include_contacts``: if ``False``, drop contact tools. This applies
      to both the agent ``CONTACT_TOOL_NAMES`` set and the human contact
      tools (``band_list_my_contacts``, etc.).

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
        include_files: Include room file tools (list/read/send). Default
            False — gated behind ``Capability.FILES`` because the platform
            endpoints they call require a deployment with file storage
            configured; on one without it every call fails.
    """
    excluded: set[str] = set()
    if not include_memory:
        excluded |= MEMORY_TOOL_NAMES
        excluded |= HUMAN_MEMORY_TOOL_NAMES
    if not include_contacts:
        excluded |= CONTACT_TOOL_NAMES
        excluded |= HUMAN_CONTACT_TOOL_NAMES
    if not include_files:
        excluded |= FILE_TOOL_NAMES

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


@dataclass(frozen=True)
class ToolCallOutcome:
    """Structured result of :meth:`AgentTools.execute_tool_call_structured`.

    ``value`` is the JSON-serializable payload handed to the LLM (the
    success result, or an error string on failure so the model can still
    react). ``ok`` is the machine-readable success flag and
    ``error_message`` the human-readable failure detail. Together they let
    callers branch on success/failure without parsing ``value`` — e.g. the
    Slack plan-progress UI marks a task ✅/❌ from ``ok`` rather than
    sniffing the error string's prefix.
    """

    value: Any
    ok: bool
    error_message: str | None = None


def serialize_tool_result(result: Any) -> Any:
    """Serialize Pydantic tool results to dicts at the adapter boundary.

    The single definition of how a tool method's return value (a Fern model,
    a list of models, or an already-plain value) becomes the JSON-serializable
    payload adapters receive. Test fakes that mirror the dispatch boundary
    (e.g. the baseline ``BaselineTools``) must use this same helper so their
    output shape cannot drift from the real one.
    """
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if isinstance(result, list):
        return [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in result
        ]
    return result


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
        agent_id: str | None = None,
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
        self._agent_id = agent_id
        self._ctx: ExecutionContext | None = None

    @property
    def agent_id(self) -> str | None:
        """This agent's own ID, used to exclude itself from mention lists."""
        return self._agent_id

    @property
    def participants(self) -> list[dict[str, Any]]:
        """Return a shallow copy of the cached participant list."""
        return list(self._participants)

    def available_mention_handles(self) -> list[str]:
        """Return handles this agent may @mention in the current room."""
        return available_mention_handles(self.participants, self._agent_id)

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
            agent_id=ctx.agent_id,
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
        from band.client.rest import (
            ChatMessageRequest,
            ChatMessageRequestMentionsItem,
        )

        # Deprecation warning for dict-style mentions WITHOUT an id: those
        # lean on name/handle resolution, which list[str] does better.
        # Id-bearing dicts are adapter-supplied ground truth (the message's
        # own sender_id) — the one shape that can never miss the
        # participants cache — and stay first-class.
        if any(isinstance(m, dict) and not m.get("id") for m in mentions or []):
            warnings.warn(
                "Passing mentions as list[dict] without an 'id' is deprecated. "
                "Use list[str] with handles instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        resolved_mentions = self._resolve_mentions(mentions or [])

        # Validate mentions are not empty — API requires ≥1 mention.
        # Return a helpful error so the LLM can retry with proper mentions.
        if not resolved_mentions:
            # Build the error through the shared hint so it carries the canonical
            # "Available handles:" marker. Adapter enrichers (CrewAI, MCP, Claude
            # SDK) re-run the same hint on this error and rely on its idempotency
            # to avoid listing the handles twice.
            raise BandToolError(
                append_mention_handles_hint(
                    "At least one mention is required",
                    self.available_mention_handles(),
                )
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
        from band.client.rest import ChatEventRequest

        logger.debug("Sending %s event to room %s", message_type, self.room_id)

        if not content:
            logger.warning(
                "Substituting placeholder for blank %s event content in room %s",
                message_type,
                self.room_id,
            )
            content = _EVENT_EMPTY_CONTENT_PLACEHOLDER

        original_length = len(content)
        content = _truncate_event_content(content)
        if len(content) != original_length:
            logger.warning(
                "Truncated oversized %s event content for room %s (%d chars > %d limit)",
                message_type,
                self.room_id,
                original_length,
                _EVENT_CONTENT_MAX_LENGTH,
            )

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

    # --- File tools ---
    #
    # The generated REST client does not expose the agent file endpoints yet,
    # so these tools speak to them through the client's own transport (same
    # base URL, auth headers, connection pool). Once the generated client
    # grows an agent files resource, migrate these calls onto it.

    def _files_transport(self) -> tuple[Any, str, dict[str, str]]:
        """Raw httpx client + base URL + auth headers from the REST client."""
        wrapper = self.rest._client_wrapper
        return (
            wrapper.httpx_client.httpx_client,
            wrapper.get_base_url().rstrip("/"),
            wrapper.get_headers(),
        )

    async def list_room_files(self) -> str:
        """List files shared in this room via messages addressed to this agent.

        The agent message index is delivery-scoped and filtered by delivery
        status. Two views matter and neither alone is enough: the message
        that triggered the current turn sits in ``processing`` until the turn
        ends, while everything older is ``processed``. ``pending`` and the
        unfiltered view cover not-yet-claimed backlog.
        """
        http, base, headers = self._files_transport()
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        # sort_order=desc because the index is oldest-first by default: without
        # it these queries return the FIRST fifty messages ever addressed to
        # this agent, and reversing a page of the oldest cannot recover the
        # newest. The platform accepts the parameter on the cursor path, which
        # is the path `limit` selects.
        for query in (
            "?limit=50&sort_order=desc&status=processing",
            "?limit=50&sort_order=desc&status=processed",
            "?limit=50&sort_order=desc&status=pending",
            "?limit=50&sort_order=desc",
        ):
            response = await http.get(
                f"{base}/api/v1/agent/chats/{self.room_id}/messages{query}",
                headers=headers,
            )
            response.raise_for_status()
            for message in response.json().get("data", []):
                if message.get("id") not in seen:
                    seen.add(message.get("id"))
                    entries.append(message)

        # Already newest-first from the server, so no reversal: reversing here
        # would put the oldest of the fetched page at the top and truncation
        # would then keep the wrong end.
        rows: list[str] = []
        for message in entries:
            for attachment in message.get("attachments") or []:
                sender = message.get("sender_name") or message.get("sender_type") or "?"
                excerpt = (message.get("content") or "").replace("\n", " ")[:60]
                # Descriptor object on current platform builds; bare id on
                # older ones.
                if isinstance(attachment, dict):
                    rows.append(
                        f"file_id={attachment.get('id')} "
                        f"name={attachment.get('name')} "
                        f"type={attachment.get('content_type')} "
                        f"bytes={attachment.get('bytes')} "
                        f'from={sender} message="{excerpt}"'
                    )
                else:
                    rows.append(
                        f'file_id={attachment} from={sender} message="{excerpt}"'
                    )
        if not rows:
            return (
                "No files found. You only see files from messages that "
                "@mentioned you — ask the sender to @mention you with the file."
            )
        return "Files in this room, newest first:\n" + "\n".join(rows[:10])

    async def read_room_file(self, file_id: str) -> Any:
        """Read a room file: text inline, images as vision input, rest described.

        Images come back as an MCP-shaped content dict (an ``image`` block
        plus a ``text`` block) so bridges that forward MCP content give the
        model actual vision input rather than base64 prose.
        """
        http, base, headers = self._files_transport()
        response = await http.get(
            f"{base}/api/v1/agent/chats/{self.room_id}/files/{file_id}",
            headers=headers,
        )
        if response.status_code == 404:
            return (
                "No such file in this room "
                "(check the file_id with band_list_room_files)."
            )
        response.raise_for_status()

        content_type = (
            response.headers.get("content-type", "application/octet-stream")
            .split(";")[0]
            .strip()
        )
        name = (
            _filename_from_disposition(response.headers.get("content-disposition"))
            or file_id
        )
        size = len(response.content)

        if content_type.startswith(_FILE_TEXTLIKE_PREFIXES):
            text = response.content[:_FILE_TEXT_INLINE_LIMIT].decode(
                "utf-8", errors="replace"
            )
            clipped = " (clipped)" if size > _FILE_TEXT_INLINE_LIMIT else ""
            return f"{name} ({content_type}, {size} bytes){clipped}:\n{text}"

        if content_type.startswith("image/") and size <= _FILE_IMAGE_INLINE_LIMIT:
            return {
                "content": [
                    {
                        "type": "image",
                        "data": base64.standard_b64encode(response.content).decode(
                            "ascii"
                        ),
                        "mimeType": content_type,
                    },
                    {
                        "type": "text",
                        "text": (
                            f"The image above is {name} ({content_type}, "
                            f"{size} bytes). Describe what you see in it."
                        ),
                    },
                ]
            }

        return (
            f"{name} is a binary file ({content_type}, {size} bytes) — you "
            "can't view this format, but you can tell the room its name, "
            "type and size."
        )

    async def send_room_file(
        self,
        filename: str,
        text_content: str,
        mention: str,
        message: str = "",
    ) -> str:
        """Upload a text file and attach it to a new message in this room.

        The mention is resolved client-side against the cached participants
        (same as ``send_message``), so this works against platforms that
        don't resolve handles server-side.

        Raises:
            ValueError: If the mention handle is not found in participants.
        """
        http, base, headers = self._files_transport()
        body = text_content.encode("utf-8")
        resolved = self._resolve_mentions([mention])

        upload = await http.put(
            f"{base}/api/v1/agent/chats/{self.room_id}/files",
            headers={
                **headers,
                "content-type": "text/plain",
                "x-file-name": filename,
                "x-file-sha256": hashlib.sha256(body).hexdigest(),
            },
            content=body,
        )
        upload.raise_for_status()
        file_id = upload.json()["data"]["id"]

        handle = resolved[0].get("handle", mention).lstrip("@")
        text = message or f"sharing {filename}"
        post = await http.post(
            f"{base}/api/v1/agent/chats/{self.room_id}/messages",
            headers=headers,
            json={
                "message": {
                    "content": f"@{handle} {text}",
                    "mentions": resolved,
                    "attachment_ids": [file_id],
                }
            },
        )
        post.raise_for_status()
        logger.debug("Shared file %s (%s) in room %s", filename, file_id, self.room_id)
        return f"Shared {filename} (file_id={file_id}) in the room."

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
        from band.runtime.context_serialization import context_item_to_dict

        response = await self.rest.agent_api_context.get_agent_chat_context(
            chat_id=room_id,
            page=page,
            page_size=page_size,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        data = [context_item_to_dict(item) for item in (response.data or [])]
        # The context response carries pagination twice: `metadata` is required,
        # `meta` is optional and may be absent. Prefer the required one, or
        # paging silently collapses to a single synthesized page.
        meta = getattr(response, "metadata", None) or getattr(response, "meta", None)
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
        from band.client.rest import ParticipantRequest

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
                "Use band_lookup_peers to find available peers."
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
        new_participant = participant_snapshot(
            {**participant.model_dump(), "name": participant_name}
        )
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

    async def lookup_peers(
        self, page: int = 1, page_size: int = 50
    ) -> ListAgentPeersResponse:
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
        refreshed = [participant_snapshot(p.model_dump()) for p in response.data]

        # Sync back to ExecutionContext so the refresh survives turn
        # boundaries. Without this, a new AgentTools built via from_context()
        # on the next turn would revert to the old participant snapshot.
        # set_participants treats the REST list as authoritative membership
        # (stale entries drop out, even ones this AgentTools never saw) while
        # merging fields per id, so a field the list endpoint omits (e.g.
        # description) is never erased once learned.
        if self._ctx is not None:
            self._ctx.set_participants(refreshed)

        self._participants = refreshed
        return response.data

    # --- Contact management tools ---

    async def list_contacts(
        self, page: int = 1, page_size: int = 50
    ) -> ListAgentContactsResponse:
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
    ) -> ListAgentContactRequestsResponse:
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
    ) -> ListAgentMemoriesResponse:
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
        scope: str,
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
        from band.client.rest import AgentMemoryCreateRequest

        validate_memory_type_for_system(system, type)
        validate_subject_scope(MemoryStoreScope(scope), subject_id)

        logger.debug(
            "Storing memory: system=%s, type=%s, segment=%s, scope=%s, subject_id=%s",
            system,
            type,
            segment,
            scope,
            subject_id,
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
            memory=AgentMemoryCreateRequest(**memory_kwargs),
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
                # Offer only real, mentionable handles to retry with: @-prefixed,
                # excluding self and handle-less participants (not the raw lookup keys).
                available_handles = self.available_mention_handles()
                raise ValueError(
                    f"Unknown participant '{identifier}'. "
                    f"{_AVAILABLE_HANDLES_MARKER} {available_handles}"
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

            # Stop when past the last page; a missing total_pages means one page
            metadata = result.metadata
            total_pages = (metadata.total_pages if metadata else None) or 1
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
        include_files: bool = False,
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
            include_files=include_files,
        ):
            schema = definition.input_model.model_json_schema()
            # Remove Pydantic-specific keys
            schema.pop("title", None)
            # Pydantic Field(ge=..., le=...) renders as JSON-Schema minimum/maximum,
            # which some providers reject on integer params (e.g. Gemini, and
            # Anthropic-backed Agno). Dropped for every format/adapter on purpose,
            # not just the strict providers: the bounds stay enforced at execution
            # via model_validate, so advertising them buys nothing.
            schema = sanitize_tool_schema(schema, drop_numeric_bounds=True)

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

        BandToolError is re-raised so framework wrappers can translate it
        into framework-native failure results. Unexpected exceptions are
        caught and returned as error strings for the LLM.

        Args:
            tool_name: Name of the tool to execute
            arguments: Arguments to pass to the tool (validated against Pydantic model)

        Returns:
            Tool execution result (dict, string, or other JSON-serializable value),
            or error string if an unexpected failure occurred

        Raises:
            BandToolError: When a tool method raises a typed tool failure
        """
        outcome = await self.execute_tool_call_structured(tool_name, arguments)
        return outcome.value

    async def execute_tool_call_structured(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolCallOutcome:
        """Execute a tool call and report success/failure structurally.

        Identical dispatch, validation, and serialization to
        :meth:`execute_tool_call`, but returns a :class:`ToolCallOutcome`
        whose ``ok`` flag is the authoritative success signal. Callers
        that need to react to failure (e.g. progress UIs) should branch on
        ``ok`` instead of inspecting the returned string, which has no
        stable error prefix. ``BandToolError`` still propagates so
        framework wrappers can translate it into native failures.
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
            return ToolCallOutcome(value=str(error), ok=False, error_message=str(error))
        except Exception as e:
            msg = f"Error validating {tool_name} arguments: {e}"
            return ToolCallOutcome(value=msg, ok=False, error_message=msg)

        definition = TOOL_DEFINITIONS.get(tool_name)
        if definition is None:
            msg = f"Unknown tool: {tool_name}"
            return ToolCallOutcome(value=msg, ok=False, error_message=msg)

        try:
            method = getattr(self, definition.method_name)
            result = await method(**arguments)
            return ToolCallOutcome(value=serialize_tool_result(result), ok=True)
        except BandToolError:
            # Let BandToolError propagate so framework wrappers can
            # translate it into framework-native failure results.
            raise
        except Exception as e:
            msg = f"Error executing {tool_name}: {e}"
            return ToolCallOutcome(value=msg, ok=False, error_message=msg)


class HumanTools:
    """User-scoped tools for Band platform interaction.

    ``HumanTools`` is stateless per credential: one instance per user-scoped
    ``AsyncRestClient``. Unlike ``AgentTools`` it is not bound to a room —
    every chat/room-bound method takes its room identifier as a plain
    ``chat_id`` argument.

    Each method is a thin wrapper around a Fern ``human_api_*`` call. The
    observable tool surface mirrors today's ``band-mcp`` human tool
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
        """Register a new remote agent owned by the user."""
        from band_rest import AgentRegisterRequest

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
        from band_rest import CreateMyChatRoomRequestChat

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
        from band_rest import CreateContactRequestRequestContactRequest

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
        from band_rest import ChatMessageRequest, ChatMessageRequestMentionsItem

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
        from band_rest import ParticipantRequest

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
