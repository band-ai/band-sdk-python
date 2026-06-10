"""
ContactTools - Standalone contact tools for CALLBACK strategy.

Unlike AgentTools which is room-bound, ContactTools is agent-level
and used for programmatic contact handling via callbacks.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from band.client.rest import AsyncRestClient

logger = logging.getLogger(__name__)


class ContactTools:
    """
    Agent-level contact tools for programmatic contact handling.

    Used by ContactEventHandler to execute contact operations in CALLBACK
    strategy. Operates at agent level (not room-bound).

    Note: ContactTools vs AgentTools
        - ContactTools: Agent-level. Used for programmatic contact handling
          in CALLBACK strategy. Contains only contact management methods.
        - AgentTools: Room-bound. Used by LLM agents in chat rooms.
          Has full tool suite including contacts, but tied to a room.

    Example:
        async def auto_approve(event: ContactEvent, tools: ContactTools) -> None:
            if isinstance(event, ContactRequestReceivedEvent):
                await tools.respond_contact_request("approve", request_id=event.payload.id)

        agent = Agent.create(
            adapter=...,
            contact_config=ContactEventConfig(
                strategy=ContactEventStrategy.CALLBACK,
                on_event=auto_approve,
            ),
        )
    """

    def __init__(self, rest: "AsyncRestClient"):
        """
        Initialize ContactTools.

        Args:
            rest: AsyncRestClient for API calls
        """
        self._rest = rest

    async def list_contacts(self, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        """
        List agent's contacts with pagination.

        Args:
            page: Page number (default 1)
            page_size: Items per page (default 50, max 100)

        Returns:
            Dict with 'contacts' list and 'metadata' (page, page_size, total_count, total_pages)
        """
        logger.debug("Listing contacts: page=%s, page_size=%s", page, page_size)
        response = await self._rest.agent_api_contacts.list_agent_contacts(
            page=page, page_size=page_size
        )

        contacts = []
        if response.data:
            contacts = [
                {
                    "id": c.id,
                    "handle": c.handle,
                    "name": c.name,
                    "type": c.type,
                }
                for c in response.data
            ]

        metadata = {
            "page": response.metadata.page if response.metadata else page,
            "page_size": response.metadata.page_size
            if response.metadata
            else page_size,
            "total_count": response.metadata.total_count
            if response.metadata
            else len(contacts),
            "total_pages": response.metadata.total_pages if response.metadata else 1,
        }

        return {"contacts": contacts, "metadata": metadata}

    async def add_contact(
        self, handle: str, message: str | None = None
    ) -> dict[str, Any]:
        """
        Send a contact request to add someone as a contact.

        Args:
            handle: Handle of user/agent to add (e.g., '@john' or '@john/agent-name')
            message: Optional message with the request

        Returns:
            Dict with id and status ('pending' or 'approved')
        """
        logger.debug("Adding contact: handle=%s", handle)
        response = await self._rest.agent_api_contacts.add_agent_contact(
            handle=handle, message=message
        )
        if not response.data:
            raise RuntimeError("Failed to add contact - no response data")
        return {
            "id": response.data.id,
            "status": response.data.status,
        }

    async def remove_contact(
        self, handle: str | None = None, contact_id: str | None = None
    ) -> dict[str, Any]:
        """
        Remove an existing contact by handle or ID.

        Args:
            handle: Contact's handle
            contact_id: Or contact record ID (UUID)

        Returns:
            Dict with status ('removed')

        Raises:
            ValueError: If neither handle nor contact_id is provided
        """
        if handle is None and contact_id is None:
            raise ValueError("Either handle or contact_id must be provided")

        logger.debug("Removing contact: handle=%s, contact_id=%s", handle, contact_id)

        # Build kwargs dynamically to avoid sending null values
        kwargs: dict[str, Any] = {}
        if handle is not None:
            kwargs["handle"] = handle
        if contact_id is not None:
            kwargs["contact_id"] = contact_id

        response = await self._rest.agent_api_contacts.remove_agent_contact(**kwargs)

        if not response.data:
            raise RuntimeError("Failed to remove contact - no response data")
        return {"status": response.data.status}

    async def list_contact_requests(
        self, page: int = 1, page_size: int = 50, sent_status: str = "pending"
    ) -> dict[str, Any]:
        """
        List both received and sent contact requests.

        Args:
            page: Page number (default 1)
            page_size: Items per page per direction (default 50, max 100)
            sent_status: Filter sent requests by status (default 'pending')

        Returns:
            Dict with 'received', 'sent' lists and 'metadata'
        """
        logger.debug(
            "Listing contact requests: page=%s, page_size=%s, sent_status=%s",
            page,
            page_size,
            sent_status,
        )
        response = await self._rest.agent_api_contacts.list_agent_contact_requests(
            page=page, page_size=page_size, sent_status=sent_status
        )

        received = []
        if response.data and response.data.received:
            received = [
                {
                    "id": r.id,
                    "from_handle": r.from_handle,
                    "from_name": r.from_name,
                    "message": r.message,
                    "status": r.status,
                    "inserted_at": str(r.inserted_at) if r.inserted_at else None,
                }
                for r in response.data.received
            ]

        sent = []
        if response.data and response.data.sent:
            sent = [
                {
                    "id": s.id,
                    "to_handle": s.to_handle,
                    "to_name": s.to_name,
                    "message": s.message,
                    "status": s.status,
                    "inserted_at": str(s.inserted_at) if s.inserted_at else None,
                }
                for s in response.data.sent
            ]

        metadata = {
            "page": response.metadata.page if response.metadata else page,
            "page_size": response.metadata.page_size
            if response.metadata
            else page_size,
            "received": {
                "total": response.metadata.received.total
                if response.metadata and response.metadata.received
                else 0,
                "total_pages": response.metadata.received.total_pages
                if response.metadata and response.metadata.received
                else 0,
            },
            "sent": {
                "total": response.metadata.sent.total
                if response.metadata and response.metadata.sent
                else 0,
                "total_pages": response.metadata.sent.total_pages
                if response.metadata and response.metadata.sent
                else 0,
            },
        }

        return {"received": received, "sent": sent, "metadata": metadata}

    async def respond_contact_request(
        self, action: str, handle: str | None = None, request_id: str | None = None
    ) -> dict[str, Any]:
        """
        Respond to a contact request (approve, reject, or cancel).

        Args:
            action: Action to take ('approve', 'reject', 'cancel')
            handle: Other party's handle
            request_id: Or request ID (UUID)

        Returns:
            Dict with id and status

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

        response = await self._rest.agent_api_contacts.respond_to_agent_contact_request(
            **kwargs
        )

        if not response.data:
            raise RuntimeError(
                "Failed to respond to contact request - no response data"
            )
        return {
            "id": response.data.id,
            "status": response.data.status,
        }
