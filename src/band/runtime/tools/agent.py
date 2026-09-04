"""
AgentTools - Tools for LLM platform interaction.

Bound to a room_id. Uses AsyncRestClient directly for API calls.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import logging
import re
import warnings
from datetime import datetime, timezone
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Iterator
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

import band_sdk_core
from async_lru import alru_cache
from pydantic import BaseModel

from band.client.rest import (
    ChatEventRequest,
    ChatMessageRequest,
    ChatMessageRequestMentionsItem,
    ChatRoomRequest,
    DEFAULT_REQUEST_OPTIONS,
    NotFoundError,
    UnprocessableEntityError,
)
from band.platform.posting import post_event, post_message
from band.config.settings import RuntimeSettings
from band.runtime.capabilities import with_hub_room_contacts
from band.runtime.participants import log_roster_call, participant_snapshot
from band.core.content import has_visible_content
from band.core.exceptions import BandToolError
from band.core.memory_types import (
    MemoryListScope,
    MemoryStoreScope,
    is_organization_scope_rejection,
    organization_scope_rejected_message,
    validate_subject_scope,
)
from band.core.protocols import AgentToolsProtocol
from band.core.task_types import TaskAssignmentStatus, TaskLifecycleState, TaskListState
from band.core.tool_filter import sanitize_tool_schema
from band.core.types import Capability
from band.runtime.tools.registry import (
    TOOL_DEFINITIONS,
    TOOL_MODELS,
    iter_tool_definitions,
    resolve_capabilities,
)
from band.runtime.tools.schema import (
    ToolCallOutcome,
    serialize_tool_result,
    validate_tool_arguments,
)

if TYPE_CHECKING:
    from anthropic.types import ToolParam

    from band.client.rest import (
        AsyncRestClient,
        Attachment,
        GetChatTaskHistoryResponse,
        ListAgentContactRequestsResponse,
        ListAgentContactsResponse,
        ListAgentMemoriesResponse,
        ListAgentPeersResponse,
        ListChatTasksResponse,
    )
    from band.runtime.execution import ExecutionContext

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


# band_send_room_file: the largest LLM-authored text file this tool accepts,
# encoded as UTF-8 bytes. Independent of the platform's 100MB upload cap --
# this bounds what an LLM composes in one tool call, not what the platform
# can store.
MAX_SEND_CONTENT_BYTES = 1_000_000

# band_read_room_file: the largest text-ish file returned inline as decoded
# text, and the largest previewable image returned inline as a base64 MCP
# image content block. Base64 inflates by ~4/3, so the image cap bounds the
# actual text the tool result carries to the model, not the file's stored
# size. Anything over its cap (or not previewable at all) gets a
# description-only result instead of bytes.
MAX_INLINE_TEXT_BYTES = 16 * 1024
MAX_INLINE_IMAGE_BYTES = 5 * 1024 * 1024

# Image content types band_read_room_file will inline -- mirrors the
# platform's own preview allowlist (`Files.@previewable_types`).
PREVIEWABLE_IMAGE_CONTENT_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)

# The platform answers an identical 404 for "file transfer is off in this
# deployment" and "wrong id / wrong room / file doesn't exist" -- there is no
# truthful way to tell those apart from the response, so one message covers
# both rather than claiming a specific cause. Shared by read_room_file's and
# send_room_file's error translation, and by the not-found case of the
# room-scan lookup below.
FILE_UNAVAILABLE_MESSAGE = (
    "File not found, or file transfer is unavailable in this room."
)

# The platform rejects blank message content even on an attachment-only
# post, so an omitted caption can't stay "".
DEFAULT_FILE_CAPTION = "Shared a file: {filename}"

# band_send_room_file's filename becomes a raw "x-file-name" HTTP header
# value: printable ASCII only. CR/LF pass a plain "is it ASCII" check but
# still break the header, so this excludes them too.
FILENAME_HEADER_SAFE_PATTERN = re.compile(r"[\x20-\x7e]+")


class AttachmentCache(Protocol):
    """The subset of async_lru's wrapper object AgentTools relies on.

    Structural, not the real ``_LRUCacheWrapper`` -- that class is private to
    async_lru (leading underscore, not exported), so naming it here would
    couple us to an implementation detail that library owes us no stability
    on.
    """

    async def __call__(
        self, room_id: str, rest: "AsyncRestClient", file_id: str
    ) -> "Attachment": ...
    def cache_invalidate(self, *args: Any, **kwargs: Any) -> bool: ...
    def cache_contains(self, *args: Any, **kwargs: Any) -> bool: ...
    def cache_info(self) -> Any: ...
    def cache_parameters(self) -> Any: ...


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
                of the ``capabilities`` argument to schema methods. The
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
        self,
        content: str,
        mentions: list[str] | list[dict[str, str]] | None = None,
        *,
        attachment_ids: list[str] | None = None,
    ) -> Any:
        """
        Send a message to the current room.

        Args:
            content: Message content to send
            mentions: List of participant handles (strings). SDK resolves handles to IDs.
                      Format: @<username> for users, @<username>/<agent-name> for agents.
                      Passing list[dict[str, str]] is deprecated; use list[str] instead.
            attachment_ids: File ids to show with this message. Not part of the
                      ``band_send_message`` tool schema -- only a Python caller
                      (e.g. ``send_room_file``) can pass this; a tool-dispatched
                      call never supplies it.

        Returns:
            Fern MessageSentResponse model (Pydantic), serialized to dict by
            execute_tool_call() at the adapter boundary, or ``None`` if
            *content* had no visible characters and the send was refused.

        Raises:
            ValueError: If a mentioned handle is not found in participants
        """
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

        resolved_mentions = self._resolve_required_mentions(mentions)

        logger.debug("Sending message to room %s", self.room_id)

        # Convert to API format - use handle (not name) for mentions
        mention_items = [
            ChatMessageRequestMentionsItem(id=m["id"], handle=m["handle"])
            for m in resolved_mentions
        ]

        # ChatMessageRequest serializes with exclude_unset=True, so an explicit
        # attachment_ids=None (the common case -- only send_room_file supplies
        # a value) would still mark the field "set" and send a literal JSON
        # null, which the platform rejects (expects an array or an absent
        # key). Omit the kwarg entirely rather than pass None.
        message_kwargs: dict[str, Any] = {"content": content, "mentions": mention_items}
        if attachment_ids is not None:
            message_kwargs["attachment_ids"] = attachment_ids

        return await post_message(
            rest=self.rest,
            room_id=self.room_id,
            request=ChatMessageRequest(**message_kwargs),
        )

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
            Fern EventCreatedResponse model (Pydantic), serialized to dict by
            execute_tool_call() at the adapter boundary, or ``None`` if
            *content* had no visible characters and the send was refused.
        """
        logger.debug("Sending %s event to room %s", message_type, self.room_id)

        return await post_event(
            rest=self.rest,
            room_id=self.room_id,
            request=ChatEventRequest(
                content=content, message_type=message_type, metadata=metadata
            ),
        )

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
        # Sync back to ExecutionContext so future turns see the update. The
        # REST add already succeeded server-side, so a local roster failure
        # must not fail this tool call after that change was applied.
        if self._ctx is not None:
            ctx = self._ctx
            log_roster_call(
                logger,
                call=ctx.add_participant,
                arg=new_participant,
                room_id=self.room_id,
            )
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
        # while retaining known fields omitted by this endpoint. Duplicate
        # ids leave the previous context roster intact and must not fail the
        # tool call.
        if self._ctx is not None:
            ctx = self._ctx
            log_roster_call(
                logger, call=ctx.set_participants, arg=refreshed, room_id=self.room_id
            )

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
            scope: Filter by scope (see MemoryListScope for valid values).
                Organization scope requires the agent's owner to belong to an
                organization; agent scope works regardless.
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
        try:
            response = await self.rest.agent_api_memories.list_agent_memories(
                **kwargs,
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        except UnprocessableEntityError as error:
            if is_organization_scope_rejection(error.body):
                raise BandToolError(
                    organization_scope_rejected_message(MemoryListScope.AGENT.value)
                ) from error
            raise

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
            scope: Visibility scope (see MemoryStoreScope for valid values).
                Organization scope requires the agent's owner to belong to an
                organization; agent scope (no subject_id) works regardless.
            subject_id: UUID of the subject (required for subject scope)
            metadata: Additional metadata (tags, references)

        Returns:
            Fern Memory model (Pydantic). Serialized to dict by
            execute_tool_call() at the adapter boundary.
        """
        from band.client.rest import AgentMemoryCreateRequest

        band_sdk_core.validate_memory_type_for_system(system, type)
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
        try:
            response = await self.rest.agent_api_memories.create_agent_memory(
                memory=AgentMemoryCreateRequest(**memory_kwargs),
                request_options=DEFAULT_REQUEST_OPTIONS,
            )
        except UnprocessableEntityError as error:
            if is_organization_scope_rejection(error.body):
                raise BandToolError(
                    organization_scope_rejected_message(MemoryStoreScope.AGENT.value)
                ) from error
            raise
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

    # --- File tools ---

    @staticmethod
    async def _list_message_page(
        room_id: str, rest: "AsyncRestClient", cursor: str | None
    ) -> Any:
        """Fetch one page of a room's message history, attachments included.

        Uses the context/rehydration endpoint, not the plain agent messages
        one: that one only ever returns messages that mention this agent,
        excluding ones it authored -- which would make a file this agent
        just sent via ``send_room_file`` undiscoverable by itself, forever.
        The context endpoint's server-side query is explicitly ``sender_id
        == agent_id OR mentions agent_id``, with no delivery-status concept
        to filter on.
        """
        kwargs: dict[str, Any] = {}
        if cursor is not None:
            kwargs["cursor"] = cursor
        return await rest.agent_api_context.get_agent_chat_context(
            chat_id=room_id,
            request_options=DEFAULT_REQUEST_OPTIONS,
            **kwargs,
        )

    @staticmethod
    def _attachments_in(messages: Collection[Any]) -> Iterator[Attachment]:
        """Yield every attachment across a page's messages, in message order."""
        for message in messages:
            yield from message.attachments or []

    @staticmethod
    async def _iter_message_pages(
        fetch: Callable[[str | None], Awaitable[Any]],
    ) -> AsyncIterator[Any]:
        """Walk every page a ``fetch(cursor)`` callable returns, oldest first.

        Termination is data-driven -- the platform's own ``has_more``/
        ``next_cursor`` on the page just fetched. Unlike ``iter_chat_pages``,
        this has no depth cap: a room's message history has no realistic
        ceiling to bound against, and a target that's merely old, not
        missing, must still be found. The only thing guarded against is a
        malformed response repeating a cursor it already returned, which
        would otherwise loop forever making no progress -- safe to key on
        the cursor value itself, since ``get_agent_chat_context`` documents
        ``cursor`` as keyset pagination (derived from the boundary row, not
        an opaque session token), so two distinct pages can't coincide.
        """
        cursor: str | None = None
        seen_cursors: set[str] = set()
        more_pages = True
        while more_pages:
            response = await fetch(cursor)
            yield response
            cursor = response.metadata.next_cursor
            more_pages = bool(response.metadata.has_more and cursor)
            if more_pages:
                if cursor in seen_cursors:
                    logger.warning(
                        "Stopped searching room history: server repeated cursor %r",
                        cursor,
                    )
                    return
                seen_cursors.add(cursor)

    @staticmethod
    async def _fetch_attachment_uncached(
        room_id: str, rest: "AsyncRestClient", file_id: str
    ) -> "Attachment":
        """Locate an attachment by id, exhausting pagination (like
        ``_lookup_peer``) instead of returning one page: the target file may
        be older than the first page, and there is no dedicated "get
        attachment by id" endpoint to reach it directly.
        """

        async def fetch(cursor: str | None) -> Any:
            return await AgentTools._list_message_page(room_id, rest, cursor)

        async for response in AgentTools._iter_message_pages(fetch):
            for attachment in AgentTools._attachments_in(response.data):
                if attachment.id == file_id:
                    return attachment
        raise BandToolError(FILE_UNAVAILABLE_MESSAGE)

    @staticmethod
    @functools.lru_cache(maxsize=None)
    def _attachment_cache() -> AttachmentCache:
        """Build the ``alru_cache``-wrapped lookup once, lazily, on first use.

        A zero-arg singleton so ``RuntimeSettings()`` (and thus
        ``BAND_ATTACHMENT_CACHE_MAXSIZE``) is read on first call, not at
        module import -- decorating ``_fetch_attachment_uncached`` directly
        would bake the maxsize in before an app's ``load_dotenv()`` has had a
        chance to run.
        """
        return alru_cache(maxsize=RuntimeSettings().BAND_ATTACHMENT_CACHE_MAXSIZE)(
            AgentTools._fetch_attachment_uncached
        )

    async def list_room_files(self, cursor: str | None = None) -> dict[str, Any]:
        """
        List files shared in the current room.

        There is no dedicated "list files" endpoint -- attachment metadata
        only exists on the messages that carry it, so this derives one bounded
        page from the room's message history (see ``_list_message_page``).

        Args:
            cursor: Pagination cursor from a previous call's response.

        Returns:
            Dict with "data" (attachment dicts, deduplicated by id -- a file
            can be attached to more than one message) and "next_cursor".
        """
        response = await self._list_message_page(self.room_id, self.rest, cursor)
        seen: set[str] = set()
        attachments: list[dict[str, Any]] = []
        for attachment in self._attachments_in(response.data):
            if attachment.id in seen:
                continue
            seen.add(attachment.id)
            attachments.append(attachment.model_dump())
        return {"data": attachments, "next_cursor": response.metadata.next_cursor}

    @staticmethod
    def _attachment_expired(attachment: "Attachment") -> bool:
        """True once ``expires_at`` has passed. A naive value (no offset --
        the Fern model doesn't enforce one) is treated as UTC, the platform's
        only timezone, rather than raising on a naive/aware comparison."""
        expires_at = attachment.expires_at
        if expires_at is None:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at <= datetime.now(timezone.utc)

    async def _find_attachment(self, file_id: str) -> "Attachment":
        """Locate an attachment by id -- see ``_attachment_cache`` for the
        cached page-walk this delegates to.

        A cached attachment past its own ``expires_at`` is evicted and
        re-fetched once before being treated as not found: the cached copy
        may simply predate the platform extending that deadline, and a
        second stale-looking read shouldn't cost more than one extra lookup.
        A still-expired refresh is evicted too, so the cache never keeps
        serving metadata it has already given up on.
        """
        cache = AgentTools._attachment_cache()
        attachment = await cache(self.room_id, self.rest, file_id)
        for attempt in range(2):
            if attempt:
                attachment = await cache(self.room_id, self.rest, file_id)
            if not self._attachment_expired(attachment):
                return attachment
            cache.cache_invalidate(self.room_id, self.rest, file_id)
        raise BandToolError(FILE_UNAVAILABLE_MESSAGE)

    async def _download_file(self, file_id: str) -> bytes:
        """Download an attachment's raw bytes, translating a 404 for the LLM."""
        try:
            chunks = [
                chunk
                async for chunk in self.rest.agent_api_files.download_agent_chat_file(
                    chat_id=self.room_id,
                    id=file_id,
                    request_options=DEFAULT_REQUEST_OPTIONS,
                )
            ]
        except NotFoundError as error:
            raise BandToolError(FILE_UNAVAILABLE_MESSAGE) from error
        return b"".join(chunks)

    async def read_room_file(self, file_id: str) -> dict[str, Any]:
        """
        Read a file shared in the current room.

        Branches on the attachment's known content type and size *before*
        downloading anything: a small text file is inlined as decoded text, a
        small previewable image is inlined as an MCP image content block, and
        everything else (too large, or not previewable) gets a
        description-only result instead of bytes.

        Args:
            file_id: File ID, from a message's attachments or
                band_list_room_files.

        Returns:
            Dict with inline "text", an MCP-shaped image "content" block, or
            a "description" summarizing why the file wasn't shown inline.
        """
        attachment = await self._find_attachment(file_id)

        match attachment.content_type:
            case ct if ct.startswith("text/"):
                kind, cap, reason = (
                    "text",
                    MAX_INLINE_TEXT_BYTES,
                    f"exceeds the {MAX_INLINE_TEXT_BYTES}-byte inline text limit",
                )
            case ct if ct in PREVIEWABLE_IMAGE_CONTENT_TYPES:
                kind, cap, reason = (
                    "image",
                    MAX_INLINE_IMAGE_BYTES,
                    f"exceeds the {MAX_INLINE_IMAGE_BYTES}-byte inline image limit",
                )
            case _:
                kind, cap, reason = (
                    None,
                    None,
                    "is not a previewable text or image type",
                )

        if kind == "text" and cap is not None and attachment.bytes <= cap:
            body = await self._download_file(file_id)
            result: dict[str, Any] = {
                "name": attachment.name,
                "content_type": attachment.content_type,
                "bytes": attachment.bytes,
            }
            # content_type has no charset (derived from magic bytes alone), so
            # a non-UTF-8 file can't be decoded correctly. Decode once with
            # replacement and detect corruption from the result rather than
            # a second strict-then-lenient decode pass over the same bytes.
            text = body.decode("utf-8", errors="replace")
            result["text"] = text
            if "�" in text:
                result["description"] = (
                    "This file is not valid UTF-8; non-UTF-8 bytes were "
                    "replaced with �, so the text above may not exactly "
                    "match the original."
                )
            return result

        if kind == "image" and cap is not None and attachment.bytes <= cap:
            body = await self._download_file(file_id)
            return {
                "content": [
                    {
                        "type": "image",
                        "data": base64.b64encode(body).decode("ascii"),
                        "mimeType": attachment.content_type,
                    }
                ]
            }

        return {
            "name": attachment.name,
            "content_type": attachment.content_type,
            "bytes": attachment.bytes,
            "description": (
                f"File not shown inline: {reason}. Its contents were not fetched."
            ),
        }

    async def send_room_file(
        self,
        content: str,
        filename: str,
        caption: str = "",
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Upload text content as a file and share it in the current room.

        Args:
            content: Text content to upload as a file.
            filename: Name for the uploaded file, including extension. Plain
                ASCII only -- it travels as a raw HTTP header value.
            caption: Optional message text to send alongside the file. A
                caption with no visible characters is replaced with a
                default -- the platform requires visible message content
                even on an attachment-only post.
            mentions: Participant handles to @mention, same format as
                band_send_message.

        Returns:
            Dict with the created attachment's metadata and the posted
            message id.
        """
        if not has_visible_content(caption):
            caption = DEFAULT_FILE_CAPTION.format(filename=filename)
        if not FILENAME_HEADER_SAFE_PATTERN.fullmatch(filename):
            raise BandToolError(
                f"Filename {filename!r} must use plain printable ASCII "
                "characters only -- the upload header cannot carry accents, "
                "CJK, emoji, line breaks, or other control characters. "
                "Rename the file and try again."
            )
        body = content.encode("utf-8")
        if len(body) > MAX_SEND_CONTENT_BYTES:
            raise BandToolError(
                f"File content is {len(body)} bytes, which exceeds the "
                f"{MAX_SEND_CONTENT_BYTES}-byte limit for band_send_room_file. "
                "Send shorter content."
            )
        # Resolve before uploading: sharing the file is a send_message call,
        # so a missing/unresolvable mention must fail before the upload,
        # not after it leaves an orphaned attachment nothing points at.
        resolved_mentions = self._resolve_required_mentions(mentions)
        sha256 = hashlib.sha256(body).hexdigest()

        try:
            upload_response = await self.rest.agent_api_files.upload_agent_chat_file(
                chat_id=self.room_id,
                request=body,
                request_options={
                    **DEFAULT_REQUEST_OPTIONS,
                    "additional_headers": {
                        "x-file-name": filename,
                        "x-file-sha256": sha256,
                        "content-type": "text/plain",
                    },
                },
            )
        except NotFoundError as error:
            raise BandToolError(FILE_UNAVAILABLE_MESSAGE) from error
        attachment = upload_response.data

        message = await self.send_message(
            content=caption,
            mentions=resolved_mentions,
            attachment_ids=[attachment.id],
        )
        return {"attachment": attachment.model_dump(), "message_id": message.id}

    # --- Task board tools ---

    async def list_tasks(
        self,
        state: TaskListState | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> "ListChatTasksResponse":
        """
        List the shared tasks on this room's task board, ordered by number.

        Args:
            state: Lifecycle filter (default: active)
            cursor: Pagination cursor from a previous call's response
            limit: Page size (default 50, max 100)

        Returns:
            Fern ListChatTasksResponse (Pydantic) with .data and .metadata.
            Serialized to dict by execute_tool_call() at the adapter boundary.
        """
        logger.debug(
            "Listing tasks: state=%s, cursor=%s, limit=%s", state, cursor, limit
        )
        response = await self.rest.agent_api_chat_tasks.list_chat_tasks(
            chat_id=self.room_id,
            state=state,
            cursor=cursor,
            limit=limit,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        return response

    async def create_task(
        self,
        subject: str,
        detail: str | None = None,
        supersedes_id: str | None = None,
    ) -> Any:
        """
        Create a shared task on this room's task board.

        Args:
            subject: What needs to be done
            detail: Longer description (optional)
            supersedes_id: UUID or board number of the active task this one
                replaces (optional)

        Returns:
            Fern Task (Pydantic) for the created task.
            Serialized to dict by execute_tool_call() at the adapter boundary.
        """
        logger.debug("Creating task: subject=%s", subject)
        kwargs: dict[str, Any] = {}
        if detail is not None:
            kwargs["detail"] = detail
        if supersedes_id is not None:
            kwargs["supersedes_id"] = supersedes_id
        response = await self.rest.agent_api_chat_tasks.create_chat_task(
            chat_id=self.room_id,
            subject=subject,
            request_options=DEFAULT_REQUEST_OPTIONS,
            **kwargs,
        )
        if not response.data:
            raise RuntimeError("Failed to create task - no response data")
        return response.data

    async def get_task(self, id: str, include: Literal["history"] | None = None) -> Any:
        """
        Read one task by UUID or board number.

        Args:
            id: Task UUID or board number
            include: Set to 'history' to embed the recent event history

        Returns:
            Fern Task (Pydantic).
            Serialized to dict by execute_tool_call() at the adapter boundary.
        """
        logger.debug("Getting task: id=%s, include=%s", id, include)
        response = await self.rest.agent_api_chat_tasks.get_chat_task(
            chat_id=self.room_id,
            id=id,
            include=include,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        if not response.data:
            raise RuntimeError("Failed to get task - no response data")
        return response.data

    async def update_task(
        self,
        id: str,
        status: TaskAssignmentStatus | None = None,
        active_form: str | None = None,
        comment: str | None = None,
        subject: str | None = None,
        detail: str | None = None,
        state: TaskLifecycleState | None = None,
    ) -> Any:
        """
        Update a task -- one operation, all fields optional.

        Args:
            id: Task UUID or board number
            status: YOUR work status on this task (first write joins you to it)
            active_form: YOUR live "doing X" sentence, shown on the board
                while you work
            comment: Append a note for the other participants
            subject: Edit the task subject
            detail: Edit the task detail
            state: Lifecycle: cancel, archive, or restore ('active' un-archives)

        Returns:
            Fern Task (Pydantic) for the full updated task.
            Serialized to dict by execute_tool_call() at the adapter boundary.
        """
        logger.debug("Updating task: id=%s", id)
        # Backstops UpdateTaskInput's model_validator: some callers (Parlant,
        # pydantic-ai) hand-register this as a plain function and never
        # construct/validate an UpdateTaskInput, so the "at least one field"
        # rule has to also live here to apply to every caller.
        if all(
            field is None
            for field in (status, active_form, comment, subject, detail, state)
        ):
            raise ValueError(
                "At least one of status, active_form, comment, subject, detail, "
                "or state must be set"
            )
        kwargs: dict[str, Any] = {}
        if status is not None:
            kwargs["status"] = status
        if active_form is not None:
            kwargs["active_form"] = active_form
        if comment is not None:
            kwargs["comment"] = comment
        if subject is not None:
            kwargs["subject"] = subject
        if detail is not None:
            kwargs["detail"] = detail
        if state is not None:
            kwargs["state"] = state
        response = await self.rest.agent_api_chat_tasks.update_chat_task(
            chat_id=self.room_id,
            id=id,
            request_options=DEFAULT_REQUEST_OPTIONS,
            **kwargs,
        )
        if not response.data:
            raise RuntimeError("Failed to update task - no response data")
        return response.data

    async def get_task_history(
        self, id: str, cursor: str | None = None, limit: int | None = None
    ) -> "GetChatTaskHistoryResponse":
        """
        The append-only history of one task, oldest first.

        Args:
            id: Task UUID or board number
            cursor: Pagination cursor from a previous call's response
            limit: Page size (default 50, max 100)

        Returns:
            Fern GetChatTaskHistoryResponse (Pydantic) with .data and .metadata.
            Serialized to dict by execute_tool_call() at the adapter boundary.
        """
        logger.debug(
            "Getting task history: id=%s, cursor=%s, limit=%s", id, cursor, limit
        )
        response = await self.rest.agent_api_chat_tasks.get_chat_task_history(
            chat_id=self.room_id,
            id=id,
            cursor=cursor,
            limit=limit,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        return response

    async def get_board(self, include: Literal["history"] | None = None) -> Any:
        """
        Read this room's goal (the team mission).

        Args:
            include: Set to 'history' to embed the goal-audit trail

        Returns:
            Fern Board (Pydantic).
            Serialized to dict by execute_tool_call() at the adapter boundary.
        """
        logger.debug("Getting board: include=%s", include)
        response = await self.rest.agent_api_chat_tasks.get_chat_board(
            chat_id=self.room_id,
            include=include,
            request_options=DEFAULT_REQUEST_OPTIONS,
        )
        if not response.data:
            raise RuntimeError("Failed to get board - no response data")
        return response.data

    async def set_board(
        self, goal_title: str | None = None, goal_summary: str | None = None
    ) -> Any:
        """
        Set or update this room's goal (upsert).

        Args:
            goal_title: The room's mission title
            goal_summary: The mission paragraph

        Returns:
            Fern Board (Pydantic) for the updated goal.
            Serialized to dict by execute_tool_call() at the adapter boundary.
        """
        logger.debug(
            "Setting board: goal_title=%s, goal_summary=%s", goal_title, goal_summary
        )
        # Backstops SetBoardInput's model_validator -- see update_task's
        # identical note on why this can't rely on the input model alone.
        if goal_title is None and goal_summary is None:
            raise ValueError("At least one of goal_title or goal_summary must be set")
        kwargs: dict[str, Any] = {}
        if goal_title is not None:
            kwargs["goal_title"] = goal_title
        if goal_summary is not None:
            kwargs["goal_summary"] = goal_summary
        response = await self.rest.agent_api_chat_tasks.put_chat_board(
            chat_id=self.room_id, request_options=DEFAULT_REQUEST_OPTIONS, **kwargs
        )
        if not response.data:
            raise RuntimeError("Failed to set board - no response data")
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

    def _resolve_required_mentions(
        self, mentions: list[str] | list[dict[str, str]] | None
    ) -> list[dict[str, str]]:
        """Resolve ``mentions``, raising if the resolved list is empty.

        Shared by ``send_message`` and ``send_room_file`` so a missing/empty
        mention list is caught before either does its side effect (posting
        the message, uploading the file) — API requires >=1 mention per
        message, and this is the single place that enforces it.
        """
        resolved = self._resolve_mentions(mentions or [])
        if not resolved:
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
        the schema methods regardless of the caller's requested capabilities.
        """
        return self._hub_room_id is not None and self.room_id == self._hub_room_id

    def get_tool_schemas(
        self,
        format: str,
        *,
        capabilities: frozenset[Capability] | None = None,
    ) -> list[dict[str, Any]] | list["ToolParam"]:
        """
        Get tool schemas in provider-specific format.

        Args:
            format: Target format - "openai" or "anthropic"
            capabilities: Which optional tool categories to include (memory,
                contacts, files). ``None`` (default) means contacts only, for
                backward compatibility. When this AgentTools is bound to the
                hub room (``self.is_hub_room``), contact tools are always
                included regardless of this argument.

        Returns:
            List of tool definitions in the requested format

        Raises:
            ValueError: If format is not "openai" or "anthropic"
        """
        if format not in ("openai", "anthropic"):
            raise ValueError(
                f"Invalid format: {format}. Must be 'openai' or 'anthropic'"
            )

        resolved = resolve_capabilities(capabilities)
        effective_capabilities = with_hub_room_contacts(
            resolved, is_hub_room=self.is_hub_room
        )

        tools: list[Any] = []
        for definition in iter_tool_definitions(capabilities=effective_capabilities):
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
        capabilities: frozenset[Capability] | None = None,
    ) -> list["ToolParam"]:
        """Get tool schemas in Anthropic format (strongly typed)."""
        return cast(
            list["ToolParam"],
            self.get_tool_schemas("anthropic", capabilities=capabilities),
        )

    def get_openai_tool_schemas(
        self,
        *,
        capabilities: frozenset[Capability] | None = None,
    ) -> list[dict[str, Any]]:
        """Get tool schemas in OpenAI format (strongly typed)."""
        return cast(
            list[dict[str, Any]],
            self.get_tool_schemas("openai", capabilities=capabilities),
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
