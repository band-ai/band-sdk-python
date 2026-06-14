"""Contact-management Parlant tools.

NOTE: Do NOT add ``from __future__ import annotations`` to this module. The
``@p.tool`` decorator inspects parameter annotations at runtime, so postponed
annotations would turn ``ToolContext``/``ToolResult`` into unresolvable strings
and break tool registration.
"""

import logging
from typing import Any

from band.integrations.parlant.tools.helpers import normalize

logger = logging.getLogger(__name__)


def build_contact_tools(
    p: Any,
    ToolContext: Any,
    ToolResult: Any,
    helpers: Any,
) -> list[Any]:
    """Build contact-management Parlant tools."""

    @p.tool
    async def band_list_contacts(
        context: ToolContext,
        page: int = 1,
        page_size: int = 50,
    ) -> ToolResult:
        """
        List agent's contacts with pagination.

        Args:
            context: Parlant tool context (automatically provided)
            page: Page number (default 1)
            page_size: Items per page (default 50, max 100)

        Returns:
            JSON with contacts list and pagination metadata
        """
        logger.info(
            "[Parlant Tool] list_contacts called: session=%s, page=%s",
            context.session_id,
            page,
        )

        async def _body(tools: Any) -> ToolResult:
            result = await tools.list_contacts(page, page_size)
            return helpers.json_result(result)

        return await helpers.execute(
            context,
            "list_contacts",
            "listing contacts",
            _body,
        )

    @p.tool
    async def band_add_contact(
        context: ToolContext,
        handle: str,
        message: str = "",
    ) -> ToolResult:
        """
        Send a contact request to add someone as a contact.

        Returns 'pending' when request is created, 'approved' when inverse
        request existed and was auto-accepted.

        Args:
            context: Parlant tool context (automatically provided)
            handle: Handle of user/agent to add (e.g., '@john' or '@john/agent-name')
            message: Optional message with the request

        Returns:
            Status of the contact request
        """
        logger.info(
            "[Parlant Tool] add_contact called: session=%s, handle=%s",
            context.session_id,
            handle,
        )

        async def _body(tools: Any) -> ToolResult:
            result = await tools.add_contact(handle, message if message else None)
            data = normalize(result)
            status = (
                data.get("status", "pending") if isinstance(data, dict) else "pending"
            )
            return ToolResult(data=f"Contact request to {handle}: {status}")

        return await helpers.execute(context, "add_contact", "adding contact", _body)

    @p.tool
    async def band_remove_contact(
        context: ToolContext,
        handle: str = "",
        contact_id: str = "",
    ) -> ToolResult:
        """
        Remove an existing contact by handle or contact ID.

        Provide either handle or contact_id (at least one required).

        Args:
            context: Parlant tool context (automatically provided)
            handle: Contact's handle (e.g., '@john')
            contact_id: Or contact record ID (UUID)

        Returns:
            Confirmation of contact removal
        """
        logger.info(
            "[Parlant Tool] remove_contact called: session=%s, handle=%s, contact_id=%s",
            context.session_id,
            handle,
            contact_id,
        )

        async def _body(tools: Any) -> ToolResult:
            h = handle if handle else None
            cid = contact_id if contact_id else None
            if not h and not cid:
                return ToolResult(
                    data="Error: Either handle or contact_id must be provided"
                )

            await tools.remove_contact(h, cid)
            identifier = handle or contact_id
            return ToolResult(data=f"Contact '{identifier}' removed successfully")

        return await helpers.execute(
            context,
            "remove_contact",
            "removing contact",
            _body,
        )

    @p.tool
    async def band_list_contact_requests(
        context: ToolContext,
        page: int = 1,
        page_size: int = 50,
        sent_status: str = "pending",
    ) -> ToolResult:
        """
        List both received and sent contact requests.

        Received requests are always filtered to pending status.
        Sent requests can be filtered by status.

        Args:
            context: Parlant tool context (automatically provided)
            page: Page number (default 1)
            page_size: Items per page per direction (default 50, max 100)
            sent_status: Filter sent requests by status: 'pending', 'approved', 'rejected', 'cancelled', or 'all'

        Returns:
            JSON with received and sent request lists and metadata
        """
        logger.info(
            "[Parlant Tool] list_contact_requests called: session=%s, sent_status=%s",
            context.session_id,
            sent_status,
        )

        async def _body(tools: Any) -> ToolResult:
            result = await tools.list_contact_requests(page, page_size, sent_status)
            return helpers.json_result(result)

        return await helpers.execute(
            context,
            "list_contact_requests",
            "listing contact requests",
            _body,
        )

    @p.tool
    async def band_respond_contact_request(
        context: ToolContext,
        action: str,
        handle: str = "",
        request_id: str = "",
    ) -> ToolResult:
        """
        Respond to a contact request.

        Actions:
        - 'approve'/'reject': For requests you RECEIVED (handle = requester's handle)
        - 'cancel': For requests you SENT (handle = recipient's handle)

        Provide either handle or request_id (at least one required).

        Args:
            context: Parlant tool context (automatically provided)
            action: Action to take - 'approve', 'reject', or 'cancel'
            handle: Other party's handle
            request_id: Or request ID (UUID)

        Returns:
            Status of the response action
        """
        logger.info(
            "[Parlant Tool] respond_contact_request called: session=%s, action=%s",
            context.session_id,
            action,
        )

        async def _body(tools: Any) -> ToolResult:
            h = handle if handle else None
            rid = request_id if request_id else None
            if not h and not rid:
                return ToolResult(
                    data="Error: Either handle or request_id must be provided"
                )

            if action not in ("approve", "reject", "cancel"):
                return ToolResult(
                    data=f"Error: Invalid action '{action}'. Use 'approve', 'reject', or 'cancel'"
                )

            result = await tools.respond_contact_request(action, h, rid)
            data = normalize(result)
            status = data.get("status", action) if isinstance(data, dict) else action
            return ToolResult(data=f"Contact request {action}d: {status}")

        return await helpers.execute(
            context,
            "respond_contact_request",
            "responding to contact request",
            _body,
        )

    return [
        band_list_contacts,
        band_add_contact,
        band_remove_contact,
        band_list_contact_requests,
        band_respond_contact_request,
    ]
