"""Fake AgentTools for unit testing adapters."""

from __future__ import annotations

import hashlib
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Literal

from band.client.rest import (
    AddAgentContactResponseData,
    AgentContact,
    AgentMemory,
    Attachment,
    Board,
    ChatMessage,
    ChatParticipant,
    EventCreatedResponse,
    GetChatTaskHistoryResponse,
    GetChatTaskHistoryResponseMetadata,
    ListAgentContactRequestsResponse,
    ListAgentContactRequestsResponseData,
    ListAgentContactRequestsResponseMetadata,
    ListAgentContactRequestsResponseMetadataReceived,
    ListAgentContactRequestsResponseMetadataSent,
    ListAgentContactsResponse,
    ListAgentContactsResponseMetadata,
    ListAgentMemoriesResponse,
    ListAgentMemoriesResponseMeta,
    ListAgentPeersResponse,
    ListAgentPeersResponseMetadata,
    ListChatTasksResponse,
    ListChatTasksResponseMetadata,
    MessageSentResponse,
    MessageSentResponseRecipientsItem,
    Peer,
    ReceivedContactRequest,
    RemoveAgentContactResponseData,
    RespondToAgentContactRequestResponseData,
    SentContactRequest,
    Task,
    TaskActor,
)
from band.core.content import has_visible_content
from band.core.exceptions import BandToolError
from band.core.task_types import TaskAssignmentStatus, TaskLifecycleState, TaskListState
from band.core.types import Capability, ContactRequestAction, ContactRequestStatus
from band.runtime.context_serialization import context_item_to_dict
from band.runtime.tools import (
    DEFAULT_FILE_CAPTION,
    FILE_UNAVAILABLE_MESSAGE,
    ParticipantAddResult,
    ParticipantRemoveResult,
    ToolCallOutcome,
    append_mention_handles_hint,
    available_mention_handles,
    matches_identifier,
    strip_handle_prefix,
)

# Synthetic identity FakeAgentTools uses for the "joins you to the task on
# first status/active_form write" semantics band_update_task documents.
_FAKE_ACTOR = TaskActor(
    id="fake-agent", name="Fake Agent", type="Agent", handle="fake-agent"
)

# Sentinel creation/update timestamp for every record this fake mints.
_FAKE_TIMESTAMP = datetime(2025, 1, 1, tzinfo=timezone.utc)


def total_pages(total: int, page_size: int) -> int:
    """Page count the platform reports for ``total`` items at ``page_size``."""
    return max(1, (total + page_size - 1) // page_size) if total else 0


def page_slice(
    items: list[dict[str, Any]], page: int, page_size: int
) -> list[dict[str, Any]]:
    """The 1-indexed page of ``items`` the platform would serve."""
    start = (page - 1) * page_size
    return items[start : start + page_size]


def _mention_recipients(
    mentions: list[str] | list[dict[str, str]] | None,
) -> list[MessageSentResponseRecipientsItem]:
    """Project raw mentions into the real send's recipients shape.

    Handle *resolution* is deliberately not mirrored here either (see
    ``send_message``'s docstring), so an id-less mention becomes its own
    handle/id rather than a resolved participant identity. Both mention
    shapes normalize their handle the same way, so the same logical mention
    produces the same recipient regardless of which shape the caller used.
    """
    recipients = []
    for mention in mentions or []:
        fields = mention if isinstance(mention, dict) else {"handle": mention}
        handle = strip_handle_prefix(fields.get("handle") or fields.get("id") or "")
        recipients.append(
            MessageSentResponseRecipientsItem(
                id=fields.get("id") or handle,
                handle=handle,
                name=fields.get("name"),
            )
        )
    return recipients


def _find_by_handle(
    records: list[dict[str, Any]],
    *,
    handle_field: str,
    handle: str,
    status: str | None = None,
) -> dict[str, Any] | None:
    """The record in ``records`` whose normalized ``handle_field`` matches
    ``handle``, or ``None``. ``status``, when given, restricts the scan to
    records currently in that status, since handle is not a unique key and
    only a currently-actionable record should resolve through it.

    Shared by ``_find_by_id_or_handle``'s handle pass and every other
    handle-only existence check in this file (``add_contact``'s
    already-a-contact and reciprocal-pending-request lookups), so the
    normalize-both-sides comparison has one definition.
    """
    candidates = (
        records if status is None else [r for r in records if r["status"] == status]
    )
    normalized_handle = strip_handle_prefix(handle)
    return next(
        (
            record
            for record in candidates
            if (stored := record.get(handle_field))
            and strip_handle_prefix(stored) == normalized_handle
        ),
        None,
    )


def _find_by_id_or_handle(
    records: list[dict[str, Any]],
    *,
    handle_field: str,
    id: str | None,
    handle: str | None,
    not_found_message: str,
    status: str | None = None,
) -> dict[str, Any]:
    """Resolve a record by id, else by normalized handle, within one store.

    An explicit ``id`` always takes precedence over ``handle`` -- checked
    against every record, unfiltered, before ``handle`` is tried at all, so
    a handle match can never shadow the record the caller actually asked
    for by id. When ``id`` names a real record whose status doesn't match
    ``status``, that is a resolved-but-not-actionable record, not a missing
    one: it fails the lookup immediately rather than falling through to a
    handle match on some unrelated record with the same status.

    Shared by every contact/request mutation that resolves its target this
    way (``remove_contact``, ``respond_contact_request``), so the lookup and
    its "no response data" failure -- the real tool's own wording when the
    backend can't find what it was asked to mutate -- stay defined once.
    """
    if id is not None:
        for record in records:
            if record["id"] == id:
                if status is None or record["status"] == status:
                    return record
                raise RuntimeError(not_found_message)
    if handle is not None:
        match = _find_by_handle(
            records, handle_field=handle_field, handle=handle, status=status
        )
        if match is not None:
            return match
    raise RuntimeError(not_found_message)


def _canonicalize_context_item(message: dict[str, Any]) -> dict[str, Any]:
    """Validate a room-context seed as ``ChatMessage`` and project it through
    the same canonicalization ``AgentTools.fetch_room_context`` applies."""
    return context_item_to_dict(ChatMessage.model_validate(message))


class FakeAgentTools:
    """
    Fake implementation of AgentToolsProtocol for testing.

    Tracks all calls and allows assertions on tool usage.
    No mocking framework needed - just use this directly.

    Example:
        async def test_adapter_sends_message():
            adapter = MyAdapter()
            tools = FakeAgentTools()

            await adapter.on_message(msg, tools, history, None,
                                     is_session_bootstrap=True, room_id="room-1")

            assert len(tools.messages_sent) == 1
            assert tools.messages_sent[0]["content"] == "Expected response"
    """

    def __init__(
        self,
        *,
        participants: list[dict[str, Any]] | None = None,
        peers: list[dict[str, Any]] | None = None,
        contacts: list[dict[str, Any]] | None = None,
        room_id: str = "room-fake",
        hub_room_id: str | None = None,
        room_context: list[dict[str, Any]] | None = None,
        memories: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
        tasks: list[dict[str, Any]] | None = None,
        board: dict[str, Any] | None = None,
        received_contact_requests: list[dict[str, Any]] | None = None,
        sent_contact_requests: list[dict[str, Any]] | None = None,
    ):
        self.room_id = room_id
        self._hub_room_id = hub_room_id
        self.messages_sent: list[dict[str, Any]] = []
        self.events_sent: list[dict[str, Any]] = []
        # Seeds are validated and canonicalized at seed time (not list time),
        # so every stored record carries the real serialized Fern model shape.
        self._participants: list[dict[str, Any]] = [
            ChatParticipant.model_validate(p).model_dump() for p in (participants or [])
        ]
        self._room_context: list[dict[str, Any]] = [
            _canonicalize_context_item(item) for item in (room_context or [])
        ]
        self._peers: list[dict[str, Any]] = [
            Peer.model_validate(peer).model_dump() for peer in (peers or [])
        ]
        self._contacts: list[dict[str, Any]] = [
            AgentContact.model_validate(contact).model_dump()
            for contact in (contacts or [])
        ]
        self._received_contact_requests: list[dict[str, Any]] = [
            ReceivedContactRequest.model_validate(request).model_dump()
            for request in (received_contact_requests or [])
        ]
        self._sent_contact_requests: list[dict[str, Any]] = [
            SentContactRequest.model_validate(request).model_dump()
            for request in (sent_contact_requests or [])
        ]
        self.memories: list[dict[str, Any]] = [
            AgentMemory.model_validate(memory).model_dump()
            for memory in (memories or [])
        ]
        self.files: list[dict[str, Any]] = [
            Attachment.model_validate(file).model_dump() for file in (files or [])
        ]
        self.tasks: list[dict[str, Any]] = [
            Task.model_validate(task).model_dump() for task in (tasks or [])
        ]
        self._task_seq: int = max((t["number"] for t in self.tasks), default=0)
        self.board: dict[str, Any] = (
            Board.model_validate(board).model_dump()
            if board is not None
            else Board(chat_room_id=self.room_id).model_dump()
        )
        self.participants_added: list[ParticipantAddResult] = []
        self.participants_removed: list[ParticipantRemoveResult] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.context_calls: list[dict[str, Any]] = []

    @property
    def is_hub_room(self) -> bool:
        """True when this fake is bound to the hub-room execution path.

        Mirrors ``AgentTools.is_hub_room`` so tests that exercise the
        HUB_ROOM auto-enable path (where contact tools are force-exposed)
        can opt in via ``FakeAgentTools(hub_room_id=..., room_id=...)``.
        """
        return self._hub_room_id is not None and self.room_id == self._hub_room_id

    async def send_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> MessageSentResponse | None:
        """Record a sent message, enforcing the platform's mention and
        visible-content requirements.

        The API rejects a mention-less message, so ``AgentTools.send_message``
        raises before any request. A fake that accepts one lets that bug pass
        every unit test and surface only in production — which it did. Handle
        *resolution* is deliberately not mirrored: a fake that dropped
        unresolvable handles would force every test to configure a participant
        roster, and it is emptiness the platform rejects universally.

        Content with no visible characters is refused the same way, returning
        ``None`` without recording anything — mirroring the real send's
        non-throwing refusal at ``band.platform.posting.post_message``.
        """
        self._require_mentions(mentions)
        if not has_visible_content(content):
            return None
        return self._record_message(content, mentions)

    def _require_mentions(
        self, mentions: list[str] | list[dict[str, str]] | None
    ) -> None:
        if not (mentions or []):
            raise BandToolError(
                append_mention_handles_hint(
                    "At least one mention is required",
                    available_mention_handles(self._participants),
                )
            )

    def _record_message(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None
    ) -> MessageSentResponse:
        message = MessageSentResponse(
            id=f"msg-{len(self.messages_sent)}",
            recipients=_mention_recipients(mentions),
            success=True,
        )
        self.messages_sent.append(
            {"id": message.id, "content": content, "mentions": mentions or []}
        )
        return message

    async def send_event(
        self,
        content: str,
        message_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> EventCreatedResponse | None:
        """Record a sent event, refusing content with no visible characters.

        Same fidelity rationale as ``send_message``: the real send returns
        ``None`` without a request rather than letting the platform 422.
        """
        if not has_visible_content(content):
            return None
        event = EventCreatedResponse(
            id=f"evt-{len(self.events_sent)}", message_type=message_type, success=True
        )
        self.events_sent.append(
            {
                "id": event.id,
                "content": content,
                "message_type": message_type,
                "metadata": metadata or {},
            }
        )
        return event

    async def add_participant(
        self, identifier: str, role: str = "member"
    ) -> ParticipantAddResult:
        """Resolve ``identifier`` against the current roster, then the seeded
        peer directory -- exactly as ``AgentTools.add_participant`` resolves
        against the platform's live roster and peer directory."""
        for cached in self._participants:
            if matches_identifier(cached, identifier):
                result = ParticipantAddResult(
                    id=cached["id"],
                    name=cached.get("name", identifier),
                    role=role,
                    status="already_in_room",
                )
                self.participants_added.append(result)
                return deepcopy(result)

        peer = next((p for p in self._peers if matches_identifier(p, identifier)), None)
        if peer is None:
            raise ValueError(
                f"Participant '{identifier}' not found. "
                "Use band_lookup_peers to find available peers."
            )

        participant_name = peer.get("name") or identifier
        participant = ChatParticipant(
            id=peer["id"],
            name=participant_name,
            handle=peer.get("handle"),
            role=role,
            status="active",
            type=peer["type"],
        ).model_dump()
        self._participants.append(participant)

        result = ParticipantAddResult(
            id=participant["id"], name=participant_name, role=role, status="added"
        )
        self.participants_added.append(result)
        return deepcopy(result)

    async def remove_participant(self, identifier: str) -> ParticipantRemoveResult:
        participant = next(
            (p for p in self._participants if matches_identifier(p, identifier)), None
        )
        if participant is None:
            raise ValueError(f"Participant '{identifier}' not found in this room.")

        self._participants.remove(participant)
        result = ParticipantRemoveResult(
            id=participant["id"],
            name=participant.get("name", identifier),
            status="removed",
        )
        self.participants_removed.append(result)
        return deepcopy(result)

    @property
    def participants(self) -> list[dict[str, Any]]:
        return list(self._participants)

    async def get_participants(self) -> list[ChatParticipant]:
        return [ChatParticipant.model_validate(p) for p in self._participants]

    async def lookup_peers(
        self, page: int = 1, page_size: int = 50
    ) -> ListAgentPeersResponse:
        """Return seeded peers in the real SDK's Fern envelope (data/metadata)."""
        return ListAgentPeersResponse(
            data=page_slice(self._peers, page, page_size),
            metadata=ListAgentPeersResponseMetadata(
                page=page,
                page_size=page_size,
                total_count=len(self._peers),
                total_pages=total_pages(len(self._peers), page_size),
            ),
        )

    async def create_chatroom(self, task_id: str | None = None) -> str:
        return f"room-{uuid.uuid4()}"

    def set_room_context(self, messages: list[dict[str, Any]]) -> None:
        """Replace the in-memory room context the fake paginates over.

        Validated and canonicalized the same way constructor seeds are, so a
        context mutated mid-test stays as faithful as one seeded up front.
        """
        self._room_context = [
            _canonicalize_context_item(message) for message in messages
        ]

    def append_room_context(self, message: dict[str, Any]) -> None:
        """Append a single message dict to the room context."""
        self._room_context.append(_canonicalize_context_item(message))

    async def fetch_room_context(
        self,
        *,
        room_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Paginate over the configured room_context list."""
        self.context_calls.append(
            {"room_id": room_id, "page": page, "page_size": page_size}
        )
        page_data = page_slice(self._room_context, page, page_size)
        total = len(self._room_context)
        return {
            "data": page_data,
            "meta": {
                "page": page,
                "page_size": page_size,
                "total_count": total,
                "total_pages": total_pages(total, page_size),
            },
        }

    async def list_contacts(
        self, page: int = 1, page_size: int = 50
    ) -> ListAgentContactsResponse:
        """Return seeded contacts in the real SDK's Fern envelope (data/metadata)."""
        return ListAgentContactsResponse(
            data=page_slice(self._contacts, page, page_size),
            metadata=ListAgentContactsResponseMetadata(
                page=page,
                page_size=page_size,
                total_count=len(self._contacts),
                total_pages=total_pages(len(self._contacts), page_size),
            ),
        )

    def _promote_received_request_to_contact(self, request: dict[str, Any]) -> None:
        """Append a contact record for a received request being approved.

        Shared by ``respond_contact_request``'s approve branch and
        ``add_contact``'s reciprocal auto-accept, so a promoted contact's
        handle normalization and required-field check are defined once.

        ReceivedContactRequest doesn't carry the requester's entity type;
        extra="allow" lets a seed attach one, defaulting to User.

        Also resolves any pending outgoing request to the same handle --
        without this, a mutual handshake (both sides request each other
        before either responds) leaves that request stuck pending forever
        alongside the now-approved contact.
        """
        from_handle = request.get("from_handle")
        if not from_handle:
            raise ValueError(
                "Failed to respond to contact request - malformed request has no from_handle"
            )
        self._contacts.append(
            AgentContact(
                id=str(uuid.uuid4()),
                handle=strip_handle_prefix(from_handle),
                name=request.get("from_name"),
                type=request.get("type", "User"),
                inserted_at=_FAKE_TIMESTAMP,
            ).model_dump()
        )
        reciprocal_sent = _find_by_handle(
            self._sent_contact_requests,
            handle_field="to_handle",
            handle=from_handle,
            status=ContactRequestStatus.PENDING,
        )
        if reciprocal_sent is not None:
            reciprocal_sent["status"] = ContactRequestStatus.APPROVED

    async def add_contact(
        self, handle: str, message: str | None = None
    ) -> AddAgentContactResponseData:
        """Create a pending outgoing request, unless the other party is
        already a contact or already sent us a pending request -- the
        latter mirrors the real handshake's reciprocal auto-accept (see
        ``AddContactInput``'s docstring). Otherwise ``list_contact_requests``
        serves the new request from the sent-request store."""
        existing_contact = _find_by_handle(
            self._contacts, handle_field="handle", handle=handle
        )
        if existing_contact is not None:
            return AddAgentContactResponseData(
                id=existing_contact["id"], status=ContactRequestStatus.APPROVED
            )

        reverse_request = _find_by_handle(
            self._received_contact_requests,
            handle_field="from_handle",
            handle=handle,
            status=ContactRequestStatus.PENDING,
        )
        if reverse_request is not None:
            reverse_request["status"] = ContactRequestStatus.APPROVED
            self._promote_received_request_to_contact(reverse_request)
            return AddAgentContactResponseData(
                id=reverse_request["id"], status=ContactRequestStatus.APPROVED
            )

        request = SentContactRequest(
            id=str(uuid.uuid4()),
            inserted_at=_FAKE_TIMESTAMP,
            message=message,
            status=ContactRequestStatus.PENDING,
            to_handle=strip_handle_prefix(handle),
        ).model_dump()
        self._sent_contact_requests.append(request)
        return AddAgentContactResponseData(
            id=request["id"], status=ContactRequestStatus.PENDING
        )

    async def remove_contact(
        self, handle: str | None = None, contact_id: str | None = None
    ) -> RemoveAgentContactResponseData:
        if handle is None and contact_id is None:
            raise ValueError("Either handle or contact_id must be provided")
        contact = _find_by_id_or_handle(
            self._contacts,
            handle_field="handle",
            id=contact_id,
            handle=handle,
            not_found_message="Failed to remove contact - no response data",
        )
        self._contacts.remove(contact)
        return RemoveAgentContactResponseData()

    async def list_contact_requests(
        self, page: int = 1, page_size: int = 50, sent_status: str = "pending"
    ) -> ListAgentContactRequestsResponse:
        """Return the real SDK's Fern envelope from the request stores.

        Received requests are always filtered to pending status, matching the
        real endpoint; sent requests are filtered by ``sent_status`` (``"all"``
        bypasses the filter).
        """
        received = [
            r
            for r in self._received_contact_requests
            if r["status"] == ContactRequestStatus.PENDING
        ]
        sent = (
            list(self._sent_contact_requests)
            if sent_status == "all"
            else [r for r in self._sent_contact_requests if r["status"] == sent_status]
        )
        return ListAgentContactRequestsResponse(
            data=ListAgentContactRequestsResponseData(
                received=page_slice(received, page, page_size),
                sent=page_slice(sent, page, page_size),
            ),
            metadata=ListAgentContactRequestsResponseMetadata(
                page=page,
                page_size=page_size,
                received=ListAgentContactRequestsResponseMetadataReceived(
                    total=len(received),
                    total_pages=total_pages(len(received), page_size),
                ),
                sent=ListAgentContactRequestsResponseMetadataSent(
                    total=len(sent), total_pages=total_pages(len(sent), page_size)
                ),
            ),
        )

    async def respond_contact_request(
        self, action: str, handle: str | None = None, request_id: str | None = None
    ) -> RespondToAgentContactRequestResponseData:
        """Approve/reject a request you received, or cancel one you sent --
        matching the real tool's two-store dispatch. Only a pending request is
        actionable, mirroring ``list_contact_requests``'s own pending-only
        filter for received requests. Approval promotes the request into the
        contact store; rejection and cancellation do not."""
        if handle is None and request_id is None:
            raise ValueError("Either handle or request_id must be provided")

        match action:
            case ContactRequestAction.APPROVE | ContactRequestAction.REJECT:
                store, handle_field = self._received_contact_requests, "from_handle"
                status = (
                    ContactRequestStatus.APPROVED
                    if action == ContactRequestAction.APPROVE
                    else ContactRequestStatus.REJECTED
                )
            case ContactRequestAction.CANCEL:
                store, handle_field, status = (
                    self._sent_contact_requests,
                    "to_handle",
                    ContactRequestStatus.CANCELLED,
                )
            case _:
                raise ValueError(f"Unknown contact request action: {action!r}")

        request = _find_by_id_or_handle(
            store,
            handle_field=handle_field,
            id=request_id,
            handle=handle,
            status=ContactRequestStatus.PENDING,
            not_found_message="Failed to respond to contact request - no response data",
        )
        if action == ContactRequestAction.APPROVE:
            self._promote_received_request_to_contact(request)
        request["status"] = status
        return RespondToAgentContactRequestResponseData(id=request["id"], status=status)

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
        """Return stored memories in the real SDK's Fern envelope (data/meta).

        Filters are accepted but not applied; ``page_size`` truncates like the
        real first page. Stored memories are already canonical serialized
        ``AgentMemory`` dicts (validated at store/seed time).
        """
        page = self.memories[:page_size]
        return ListAgentMemoriesResponse(
            data=page,
            meta=ListAgentMemoriesResponseMeta(
                page_size=len(page), total_count=len(self.memories)
            ),
        )

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
    ) -> dict[str, Any]:
        """Store and return the memory in the real serialized AgentMemory shape."""
        memory = AgentMemory(
            id=str(uuid.uuid4()),
            content=content,
            system=system,
            type=type,
            segment=segment,
            scope=scope,
            status="active",
            thought=thought,
            subject_id=subject_id,
            metadata=metadata,
            inserted_at=_FAKE_TIMESTAMP,
        ).model_dump()
        self.memories.append(memory)
        return deepcopy(memory)

    async def get_memory(self, memory_id: str) -> dict[str, Any]:
        """Return a copy of the stored memory; unknown ids raise like the real tool."""
        memory = next((m for m in self.memories if m["id"] == memory_id), None)
        if memory is None:
            raise RuntimeError("Failed to get memory - no response data")
        return deepcopy(memory)

    async def supersede_memory(self, memory_id: str) -> dict[str, Any]:
        return self._set_memory_status(memory_id, "superseded", "supersede")

    async def archive_memory(self, memory_id: str) -> dict[str, Any]:
        return self._set_memory_status(memory_id, "archived", "archive")

    def _set_memory_status(
        self, memory_id: str, status: str, action: str
    ) -> dict[str, Any]:
        for memory in self.memories:
            if memory["id"] == memory_id:
                memory["status"] = status
                return deepcopy(memory)
        raise RuntimeError(f"Failed to {action} memory - no response data")

    @property
    def memory_contents(self) -> list[str]:
        """Contents of the stored memories, oldest first — a readable
        projection for test assertions."""
        return [memory["content"] for memory in self.memories]

    async def list_room_files(self, cursor: str | None = None) -> dict[str, Any]:
        return {"data": [deepcopy(file) for file in self.files], "next_cursor": None}

    async def read_room_file(self, file_id: str) -> dict[str, Any]:
        """Return a description-only result; the fake never fabricates bytes."""
        file = next((f for f in self.files if f["id"] == file_id), None)
        if file is None:
            raise BandToolError(FILE_UNAVAILABLE_MESSAGE)
        return {
            "name": file["name"],
            "content_type": file["content_type"],
            "bytes": file["bytes"],
            "description": (
                f"Fake file '{file['name']}' ({file['content_type']}, "
                f"{file['bytes']} bytes)."
            ),
        }

    async def send_room_file(
        self,
        content: str,
        filename: str,
        caption: str = "",
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Store the file and post it as a message, in the real tool's order:
        mentions are validated before the file is recorded, so a rejected
        call leaves no orphaned upload behind. A caption with no visible
        characters falls back to the default -- the send would refuse it
        otherwise, leaving nothing to report a message id from."""
        self._require_mentions(mentions)
        if not has_visible_content(caption):
            caption = DEFAULT_FILE_CAPTION.format(filename=filename)
        body = content.encode("utf-8")
        attachment = Attachment(
            id=str(uuid.uuid4()),
            name=filename,
            content_type="text/plain",
            bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            has_thumb=False,
        ).model_dump()
        message = self._record_message(caption, mentions)
        self.files.append(attachment)
        return {"attachment": deepcopy(attachment), "message_id": message.id}

    def _find_task(self, id: str) -> dict[str, Any]:
        task = next(
            (t for t in self.tasks if t["id"] == id or str(t["number"]) == id), None
        )
        if task is None:
            raise RuntimeError(f"Failed to find task {id!r} - no response data")
        return task

    async def list_tasks(
        self,
        state: TaskListState | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> ListChatTasksResponse:
        """Return seeded tasks in the real SDK's Fern envelope (data/metadata).

        Defaults to active tasks, like the real endpoint; "all" returns every
        lifecycle state. No cursor pagination -- the fake returns everything
        that matches in one page.
        """
        resolved_state = state or TaskListState.ACTIVE
        matching = (
            list(self.tasks)
            if resolved_state == TaskListState.ALL
            else [t for t in self.tasks if t["state"] == resolved_state]
        )
        return ListChatTasksResponse(
            data=matching,
            metadata=ListChatTasksResponseMetadata(
                has_more=False, limit=limit or 50, next_cursor=None
            ),
        )

    async def create_task(
        self,
        subject: str,
        detail: str | None = None,
        supersedes_id: str | None = None,
    ) -> dict[str, Any]:
        """Create and store a task in the real serialized Task shape.

        Never auto-assigned, matching the real tool -- call update_task to
        join. ``supersedes_id`` marks the replaced task "superseded" and
        points its ``superseded_by_id`` at the new task, like the real API.
        """
        self._task_seq += 1
        now = _FAKE_TIMESTAMP
        new_id = str(uuid.uuid4())
        task = Task(
            id=new_id,
            number=self._task_seq,
            chat_room_id=self.room_id,
            subject=subject,
            detail=detail or "",
            state="active",
            overall_status="pending",
            assignments=[],
            created_by=_FAKE_ACTOR,
            inserted_at=now,
            updated_at=now,
        ).model_dump()
        self.tasks.append(task)
        if supersedes_id is not None:
            old_task = self._find_task(supersedes_id)
            old_task["state"] = "superseded"
            old_task["superseded_by_id"] = new_id
        return deepcopy(task)

    async def get_task(
        self, id: str, include: Literal["history"] | None = None
    ) -> dict[str, Any]:
        return deepcopy(self._find_task(id))

    async def update_task(
        self,
        id: str,
        status: TaskAssignmentStatus | None = None,
        active_form: str | None = None,
        comment: str | None = None,
        subject: str | None = None,
        detail: str | None = None,
        state: TaskLifecycleState | None = None,
    ) -> dict[str, Any]:
        """Apply the given fields to the stored task, joining the fake actor's
        assignment on first status/active_form write, like the real tool."""
        task = self._find_task(id)
        now = _FAKE_TIMESTAMP
        if subject is not None:
            task["subject"] = subject
        if detail is not None:
            task["detail"] = detail
        if state is not None:
            task["state"] = state
        if status is not None or active_form is not None:
            assignment = next(
                (
                    a
                    for a in task["assignments"]
                    if a["assignee"]["id"] == _FAKE_ACTOR.id
                ),
                None,
            )
            if assignment is None:
                assignment = {
                    "assignee": _FAKE_ACTOR.model_dump(),
                    "status": "pending",
                    "active_form": "",
                    "linked_native_id": "",
                    "updated_at": now,
                }
                task["assignments"].append(assignment)
            if status is not None:
                assignment["status"] = status
                task["overall_status"] = status
            if active_form is not None:
                assignment["active_form"] = active_form
            assignment["updated_at"] = now
        task["updated_at"] = now
        return deepcopy(task)

    async def get_task_history(
        self, id: str, cursor: str | None = None, limit: int | None = None
    ) -> GetChatTaskHistoryResponse:
        """Return an empty history envelope; the fake tracks no event ledger."""
        self._find_task(id)
        return GetChatTaskHistoryResponse(
            data=[],
            metadata=GetChatTaskHistoryResponseMetadata(
                has_more=False, limit=limit or 50, next_cursor=None
            ),
        )

    async def get_board(
        self, include: Literal["history"] | None = None
    ) -> dict[str, Any]:
        return deepcopy(self.board)

    async def set_board(
        self, goal_title: str | None = None, goal_summary: str | None = None
    ) -> dict[str, Any]:
        now = _FAKE_TIMESTAMP
        if goal_title is not None:
            self.board["goal_title"] = goal_title
        if goal_summary is not None:
            self.board["goal_summary"] = goal_summary
        self.board["updated_at"] = now
        self.board["updated_by"] = _FAKE_ACTOR.model_dump()
        return deepcopy(self.board)

    def get_tool_schemas(
        self,
        format: str,
        *,
        capabilities: frozenset[Capability] | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def get_anthropic_tool_schemas(
        self,
        *,
        capabilities: frozenset[Capability] | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def get_openai_tool_schemas(
        self,
        *,
        capabilities: frozenset[Capability] | None = None,
    ) -> list[dict[str, Any]]:
        return []

    async def execute_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        return (await self.execute_tool_call_structured(tool_name, arguments)).value

    async def execute_tool_call_structured(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolCallOutcome:
        """Record the call and report success. Override in a subclass to return
        ``ok=False`` (a base tool failing without raising) for failure-path tests."""
        self.tool_calls.append({"tool_name": tool_name, "arguments": arguments})
        return ToolCallOutcome(value={"status": "ok"}, ok=True)

    # --- Assertion helpers ---

    def assert_message_sent(
        self,
        *,
        content: str | None = None,
        mentions: list[str] | None = None,
        count: int | None = None,
    ) -> None:
        """Assert that a message was sent, optionally matching content/mentions/count."""
        if count is not None:
            assert len(self.messages_sent) == count, (
                f"Expected {count} messages, got {len(self.messages_sent)}"
            )
        if content is not None:
            matching = [m for m in self.messages_sent if m["content"] == content]
            assert matching, (
                f"No message with content {content!r} found. "
                f"Sent: {[m['content'] for m in self.messages_sent]}"
            )
        if mentions is not None:
            matching = [m for m in self.messages_sent if m["mentions"] == mentions]
            assert matching, (
                f"No message with mentions {mentions!r} found. "
                f"Sent: {[m['mentions'] for m in self.messages_sent]}"
            )

    def assert_event_sent(
        self,
        *,
        message_type: str | None = None,
        count: int | None = None,
    ) -> None:
        """Assert that an event was sent; ``count`` counts events of
        ``message_type`` when one is given, otherwise all events."""
        matching = [
            e
            for e in self.events_sent
            if message_type is None or e["message_type"] == message_type
        ]
        if count is None and message_type is None:
            assert matching, "Expected at least one event; none were sent"
        if count is not None:
            assert len(matching) == count, (
                f"Expected {count} {message_type or 'total'} events, "
                f"got {len(matching)}. "
                f"Sent types: {[e['message_type'] for e in self.events_sent]}"
            )
        if message_type is not None:
            assert matching, (
                f"No event with type {message_type!r} found. "
                f"Sent types: {[e['message_type'] for e in self.events_sent]}"
            )

    def assert_no_messages_sent(self) -> None:
        """Assert that no messages were sent."""
        assert not self.messages_sent, (
            f"Expected no messages, but {len(self.messages_sent)} were sent"
        )
