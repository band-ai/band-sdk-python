"""
Parlant tool definitions that wrap Thenvoi AgentTools.

These tools are defined at server startup and use a session-keyed registry
to access the current room's tools during execution.

This module provides the same tools as LangGraph/Claude adapters:
- send_message: Send messages to the chat room
- send_event: Send events (thought, error, task)
- add_participant: Add agents/users to the room
- remove_participant: Remove participants
- lookup_peers: Find available agents
- get_participants: List current participants
- create_chatroom: Create new rooms
- list_contacts: List agent's contacts
- add_contact: Send a contact request
- remove_contact: Remove an existing contact
- list_contact_requests: List received and sent requests
- respond_contact_request: Approve, reject, or cancel requests

NOTE: We intentionally do NOT use `from __future__ import annotations` here
because Parlant's @p.tool decorator checks annotation types at runtime.
"""

import json
import logging
import warnings
from typing import Any, Optional

from thenvoi.core.types import AdapterFeatures, Capability

logger = logging.getLogger(__name__)

# Session-keyed registry to hold tools for each session
# This approach works across async contexts (unlike ContextVar)
_session_tools: dict[str, Any] = {}

# Track whether send_message was called for each session
# This helps the adapter know if it needs to forward Parlant's response
_session_message_sent: dict[str, bool] = {}


def set_session_tools(session_id: str, tools: Optional[Any]) -> None:
    """Set the tools for a specific Parlant session."""
    if tools is None:
        _session_tools.pop(session_id, None)
        _session_message_sent.pop(session_id, None)
    else:
        _session_tools[session_id] = tools
        _session_message_sent[session_id] = False
    logger.debug("Set tools for session %s: %s", session_id, tools is not None)


def get_session_tools(session_id: str) -> Optional[Any]:
    """Get the tools for a specific Parlant session."""
    tools = _session_tools.get(session_id)
    logger.debug(
        "Get tools for session_id=%s: found=%s, available_sessions=%s",
        session_id,
        tools is not None,
        list(_session_tools.keys()),
    )
    return tools


def mark_message_sent(session_id: str) -> None:
    """Mark that a message was sent via the send_message tool for this session."""
    _session_message_sent[session_id] = True
    logger.debug("Marked message sent for session %s", session_id)


def was_message_sent(session_id: str) -> bool:
    """Check if a message was sent via the send_message tool for this session."""
    return _session_message_sent.get(session_id, False)


# Keep old API for backwards compatibility (deprecated)
def set_current_tools(tools: Optional[Any]) -> None:
    """Deprecated: Use set_session_tools instead."""
    warnings.warn(
        "set_current_tools is deprecated, use set_session_tools instead",
        DeprecationWarning,
        stacklevel=2,
    )


def get_current_tools() -> Optional[Any]:
    """Deprecated: Use get_session_tools instead."""
    warnings.warn(
        "get_current_tools is deprecated, use get_session_tools instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return None  # Always returns None, tools now accessed via session_id


def create_parlant_tools(features: AdapterFeatures | None = None) -> list[Any]:
    """Create Parlant tool definitions that wrap Thenvoi tools.

    These tools use context variables to access the current room's
    AgentToolsProtocol during execution.

    Args:
        features: Optional adapter features. When CONTACTS capability is absent,
            contact-management tools are excluded from the returned list.

    Returns:
        List of Parlant ToolEntry objects
    """
    try:
        import parlant.sdk as p  # type: ignore[missing-import]
        from parlant.core.tools import ToolContext, ToolResult  # type: ignore[missing-import]
    except ImportError:
        logger.warning("Parlant SDK not installed, skipping tool creation")
        return []

    @p.tool
    async def thenvoi_send_message(
        context: ToolContext,
        content: str,
        mentions: str,
    ) -> ToolResult:
        """
        Send a message to the chat room.

        Use this to respond to users or other agents. Messages require @mentions
        to reach users. You MUST use this tool to communicate.

        Args:
            context: Parlant tool context (automatically provided)
            content: The message content to send
            mentions: Comma-separated list of participant handles to @mention (e.g., "@alice, @bob/agent")

        Returns:
            Confirmation of message sent or error
        """
        logger.info(
            "[Parlant Tool] send_message called: session=%s, content=%s..., mentions=%s",
            context.session_id,
            content[:50],
            mentions,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] send_message: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            # Parse mentions from comma-separated string
            mention_list = [m.strip() for m in mentions.split(",") if m.strip()]
            if not mention_list:
                logger.warning("[Parlant Tool] send_message: No mentions provided")
                return ToolResult(data="Error: At least one mention is required")

            logger.info("[Parlant Tool] Sending message to: %s", mention_list)
            await tools.send_message(content, mention_list)
            # Mark that we sent a message via the tool (so adapter doesn't duplicate)
            mark_message_sent(context.session_id)
            logger.info("[Parlant Tool] Message sent successfully via tool")
            return ToolResult(data=f"Message sent to {', '.join(mention_list)}")
        except Exception as e:
            logger.error("[Parlant Tool] Error sending message: %s", e, exc_info=True)
            return ToolResult(data=f"Error sending message: {e}")

    @p.tool
    async def thenvoi_send_event(
        context: ToolContext,
        content: str,
        message_type: str,
    ) -> ToolResult:
        """
        Send an event to the chat room. No mentions required.

        Use this to share your reasoning or report status.

        Args:
            context: Parlant tool context (automatically provided)
            content: Human-readable event content
            message_type: Type of event - 'thought' (share reasoning), 'error' (report problem), or 'task' (report progress)

        Returns:
            Confirmation of event sent or error
        """
        logger.info(
            "[Parlant Tool] send_event called: session=%s, type=%s",
            context.session_id,
            message_type,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] send_event: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        if message_type not in ("thought", "error", "task"):
            return ToolResult(
                data=f"Error: Invalid message_type '{message_type}'. Use 'thought', 'error', or 'task'"
            )

        try:
            await tools.send_event(content, message_type, None)
            logger.info("[Parlant Tool] Event (%s) sent successfully", message_type)
            return ToolResult(data=f"Event ({message_type}) sent successfully")
        except Exception as e:
            logger.error("[Parlant Tool] Error sending event: %s", e, exc_info=True)
            return ToolResult(data=f"Error sending event: {e}")

    @p.tool
    async def thenvoi_add_participant(
        context: ToolContext,
        identifier: str,
    ) -> ToolResult:
        """
        Invite an agent or user to join this chat room.

        Args:
            context: Parlant tool context (automatically provided)
            identifier: REQUIRED - Handle, name, or ID of the agent to add. Prefer the exact ID returned by lookup_peers; handles are mainly for mentions. Use lookup_peers to find available agents.

        Returns:
            Success message or error description

        Example calls:
            add_participant(identifier="pirate-captain")
            add_participant(identifier="Research Agent")
        """
        logger.info(
            "[Parlant Tool] add_participant called: session=%s, identifier=%s",
            context.session_id,
            identifier,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] add_participant: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            result = await tools.add_participant(identifier, "member")
            status = result.get("status", "added")
            if status == "already_in_room":
                logger.info("[Parlant Tool] '%s' is already in the room", identifier)
                return ToolResult(
                    data=f"'{identifier}' is already in the room - no action needed"
                )
            logger.info(
                "[Parlant Tool] Successfully added '%s' to the room", identifier
            )
            return ToolResult(data=f"Successfully added '{identifier}' to the room")
        except Exception as e:
            logger.error(
                "[Parlant Tool] Error adding participant '%s': %s",
                identifier,
                e,
                exc_info=True,
            )
            return ToolResult(data=f"Error adding participant '{identifier}': {e}")

    @p.tool
    async def thenvoi_remove_participant(
        context: ToolContext,
        identifier: str,
    ) -> ToolResult:
        """
        Remove a participant from this chat room.

        Args:
            context: Parlant tool context (automatically provided)
            identifier: REQUIRED - Handle, name, or ID of the participant to remove.

        Returns:
            Success message or error description

        Example calls:
            remove_participant(identifier="pirate-captain")
            remove_participant(identifier="Research Agent")
        """
        logger.info(
            "[Parlant Tool] remove_participant called: session=%s, identifier=%s",
            context.session_id,
            identifier,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] remove_participant: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            await tools.remove_participant(identifier)
            logger.info(
                "[Parlant Tool] Successfully removed '%s' from the room", identifier
            )
            return ToolResult(data=f"Successfully removed '{identifier}' from the room")
        except Exception as e:
            logger.error(
                "[Parlant Tool] Error removing participant '%s': %s",
                identifier,
                e,
                exc_info=True,
            )
            return ToolResult(data=f"Error removing participant '{identifier}': {e}")

    @p.tool
    async def thenvoi_lookup_peers(
        context: ToolContext,
    ) -> ToolResult:
        """
        List available peers (agents and users) that can be added to this room.

        Automatically excludes peers already in the room. Use this to find
        specialized agents when you cannot answer a question directly.

        Args:
            context: Parlant tool context (automatically provided)

        Returns:
            List of available agents with their names and descriptions
        """
        logger.info(
            "[Parlant Tool] lookup_peers called: session=%s", context.session_id
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] lookup_peers: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            # Use defaults - pagination rarely needed for agent lookups
            result = await tools.lookup_peers(page=1, page_size=50)
            logger.info("[Parlant Tool] lookup_peers result: %s", result)
            # Normalize Fern model -> dict for uniform handling
            data = result.model_dump() if hasattr(result, "model_dump") else result
            peers = data.get("data") or data.get("peers") or []
            metadata = data.get("metadata") or {}
            if not peers:
                return ToolResult(data="No available agents found")

            page_num = metadata.get("page", 1)
            total_pages = metadata.get("total_pages", 1)
            lines = [f"Available agents (page {page_num} of {total_pages}):"]
            for peer in peers:
                name = peer.get("name", "Unknown")
                desc = peer.get("description") or "No description"
                peer_type = peer.get("type", "Agent")
                lines.append(f"- {name} ({peer_type}): {desc}")
            return ToolResult(data="\n".join(lines))
        except Exception as e:
            logger.error("[Parlant Tool] Error looking up peers: %s", e, exc_info=True)
            return ToolResult(data=f"Error looking up peers: {e}")

    @p.tool
    async def thenvoi_get_participants(
        context: ToolContext,
    ) -> ToolResult:
        """
        Get the list of all participants currently in the chat room.

        Args:
            context: Parlant tool context (automatically provided)

        Returns:
            List of current participants with their names and types
        """
        logger.info(
            "[Parlant Tool] get_participants called: session=%s", context.session_id
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] get_participants: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            result = await tools.get_participants()
            logger.info("[Parlant Tool] get_participants result: %s", result)
            # Normalize Fern models -> dicts for uniform handling
            if isinstance(result, list):
                items = [
                    p.model_dump() if hasattr(p, "model_dump") else p for p in result
                ]
                if not items:
                    return ToolResult(data="No participants in the room")
                lines = ["Current participants:"]
                for participant in items:
                    name = participant.get("name", "Unknown")
                    p_type = participant.get("type", "Unknown")
                    lines.append(f"- {name} ({p_type})")
                return ToolResult(data="\n".join(lines))
            return ToolResult(data=str(result))
        except Exception as e:
            logger.error(
                "[Parlant Tool] Error getting participants: %s", e, exc_info=True
            )
            return ToolResult(data=f"Error getting participants: {e}")

    @p.tool
    async def thenvoi_create_chatroom(
        context: ToolContext,
        task_id: str = "",
    ) -> ToolResult:
        """
        Create a new chat room for a specific task or conversation.

        Args:
            context: Parlant tool context (automatically provided)
            task_id: Optional task ID to associate with the room

        Returns:
            The ID of the newly created room
        """
        logger.info(
            "[Parlant Tool] create_chatroom called: session=%s, task_id=%s",
            context.session_id,
            task_id,
        )
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] create_chatroom: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            result = await tools.create_chatroom(task_id if task_id else None)
            logger.info("[Parlant Tool] Created chatroom: %s", result)
            return ToolResult(data=f"Created new chat room: {result}")
        except Exception as e:
            logger.error("[Parlant Tool] Error creating chatroom: %s", e, exc_info=True)
            return ToolResult(data=f"Error creating chatroom: {e}")

    include_contacts = features is None or Capability.CONTACTS in features.capabilities

    @p.tool
    async def thenvoi_list_contacts(
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
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] list_contacts: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            result = await tools.list_contacts(page, page_size)
            # Fern model: serialize via model_dump if available, fallback to str
            data = result.model_dump() if hasattr(result, "model_dump") else result
            return ToolResult(data=json.dumps(data, default=str))
        except Exception as e:
            logger.error("[Parlant Tool] Error listing contacts: %s", e, exc_info=True)
            return ToolResult(data=f"Error listing contacts: {e}")

    @p.tool
    async def thenvoi_add_contact(
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
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] add_contact: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            result = await tools.add_contact(handle, message if message else None)
            data = result.model_dump() if hasattr(result, "model_dump") else result
            status = (
                data.get("status", "pending") if isinstance(data, dict) else "pending"
            )
            return ToolResult(data=f"Contact request to {handle}: {status}")
        except Exception as e:
            logger.error("[Parlant Tool] Error adding contact: %s", e, exc_info=True)
            return ToolResult(data=f"Error adding contact: {e}")

    @p.tool
    async def thenvoi_remove_contact(
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
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] remove_contact: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        h = handle if handle else None
        cid = contact_id if contact_id else None
        if not h and not cid:
            return ToolResult(
                data="Error: Either handle or contact_id must be provided"
            )

        try:
            await tools.remove_contact(h, cid)
            identifier = handle or contact_id
            return ToolResult(data=f"Contact '{identifier}' removed successfully")
        except Exception as e:
            logger.error("[Parlant Tool] Error removing contact: %s", e, exc_info=True)
            return ToolResult(data=f"Error removing contact: {e}")

    @p.tool
    async def thenvoi_list_contact_requests(
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
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] list_contact_requests: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

        try:
            result = await tools.list_contact_requests(page, page_size, sent_status)
            # Fern model: serialize via model_dump if available, fallback to str
            data = result.model_dump() if hasattr(result, "model_dump") else result
            return ToolResult(data=json.dumps(data, default=str))
        except Exception as e:
            logger.error(
                "[Parlant Tool] Error listing contact requests: %s", e, exc_info=True
            )
            return ToolResult(data=f"Error listing contact requests: {e}")

    @p.tool
    async def thenvoi_respond_contact_request(
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
        tools = get_session_tools(context.session_id)
        if not tools:
            logger.error(
                "[Parlant Tool] respond_contact_request: No tools available for session %s",
                context.session_id,
            )
            return ToolResult(data="Error: No tools available in current context")

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

        try:
            result = await tools.respond_contact_request(action, h, rid)
            data = result.model_dump() if hasattr(result, "model_dump") else result
            status = data.get("status", action) if isinstance(data, dict) else action
            return ToolResult(data=f"Contact request {action}d: {status}")
        except Exception as e:
            logger.error(
                "[Parlant Tool] Error responding to contact request: %s",
                e,
                exc_info=True,
            )
            return ToolResult(data=f"Error responding to contact request: {e}")

    tools = [
        thenvoi_send_message,
        thenvoi_send_event,
        thenvoi_add_participant,
        thenvoi_remove_participant,
        thenvoi_lookup_peers,
        thenvoi_get_participants,
        thenvoi_create_chatroom,
    ]

    if include_contacts:
        tools.extend(
            [
                thenvoi_list_contacts,
                thenvoi_add_contact,
                thenvoi_remove_contact,
                thenvoi_list_contact_requests,
                thenvoi_respond_contact_request,
            ]
        )

    return tools
