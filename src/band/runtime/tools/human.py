"""
HumanTools - User-scoped tools for Band platform interaction.

Stateless per credential; not bound to a room like ``AgentTools`` is.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from band.client.rest import DEFAULT_REQUEST_OPTIONS

if TYPE_CHECKING:
    from band.client.rest import AsyncRestClient

logger = logging.getLogger(__name__)


class HumanTools:
    """User-scoped tools for Band platform interaction.

    ``HumanTools`` is stateless per credential: one instance per user-scoped
    ``AsyncRestClient``. Unlike ``AgentTools`` it is not bound to a room —
    every chat/room-bound method takes its room identifier as a plain
    ``chat_id`` argument.

    Each method is a thin wrapper around a Fern ``human_api_*`` call. The
    observable tool surface mirrors ``band-mcp``'s human tool handlers
    verbatim; widening to full Fern parity is explicitly out of scope.
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
