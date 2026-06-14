"""Chat and room-management Parlant tools.

NOTE: Do NOT add ``from __future__ import annotations`` to this module. The
``@p.tool`` decorator inspects parameter annotations at runtime, so postponed
annotations would turn ``ToolContext``/``ToolResult`` into unresolvable strings
and break tool registration.
"""

import logging
from typing import Any

from band.integrations.parlant.tools.helpers import normalize
from band.integrations.parlant.tools.registry import mark_message_sent

logger = logging.getLogger(__name__)


def build_chat_tools(
    p: Any,
    ToolContext: Any,
    ToolResult: Any,
    helpers: Any,
) -> list[Any]:
    """Build chat and room-management Parlant tools."""

    @p.tool
    async def band_send_message(
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

        async def _body(tools: Any) -> ToolResult:
            mention_list = [m.strip() for m in mentions.split(",") if m.strip()]
            if not mention_list:
                logger.warning("[Parlant Tool] send_message: No mentions provided")
                return ToolResult(data="Error: At least one mention is required")

            logger.info("[Parlant Tool] Sending message to: %s", mention_list)
            await tools.send_message(content, mention_list)
            mark_message_sent(context.session_id)
            logger.info("[Parlant Tool] Message sent successfully via tool")
            return ToolResult(data=f"Message sent to {', '.join(mention_list)}")

        return await helpers.execute(
            context,
            "send_message",
            "sending message",
            _body,
        )

    @p.tool
    async def band_send_event(
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

        async def _body(tools: Any) -> ToolResult:
            if message_type not in ("thought", "error", "task"):
                return ToolResult(
                    data=f"Error: Invalid message_type '{message_type}'. Use 'thought', 'error', or 'task'"
                )

            await tools.send_event(content, message_type, None)
            logger.info("[Parlant Tool] Event (%s) sent successfully", message_type)
            return ToolResult(data=f"Event ({message_type}) sent successfully")

        return await helpers.execute(context, "send_event", "sending event", _body)

    @p.tool
    async def band_add_participant(
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

        async def _body(tools: Any) -> ToolResult:
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

        return await helpers.execute(
            context,
            "add_participant",
            f"adding participant '{identifier}'",
            _body,
        )

    @p.tool
    async def band_remove_participant(
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

        async def _body(tools: Any) -> ToolResult:
            await tools.remove_participant(identifier)
            logger.info(
                "[Parlant Tool] Successfully removed '%s' from the room", identifier
            )
            return ToolResult(data=f"Successfully removed '{identifier}' from the room")

        return await helpers.execute(
            context,
            "remove_participant",
            f"removing participant '{identifier}'",
            _body,
        )

    @p.tool
    async def band_lookup_peers(
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

        async def _body(tools: Any) -> ToolResult:
            result = await tools.lookup_peers(page=1, page_size=50)
            logger.info("[Parlant Tool] lookup_peers result: %s", result)
            data = normalize(result)
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

        return await helpers.execute(
            context,
            "lookup_peers",
            "looking up peers",
            _body,
        )

    @p.tool
    async def band_get_participants(
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

        async def _body(tools: Any) -> ToolResult:
            result = await tools.get_participants()
            logger.info("[Parlant Tool] get_participants result: %s", result)
            if isinstance(result, list):
                items = [normalize(participant) for participant in result]
                if not items:
                    return ToolResult(data="No participants in the room")
                lines = ["Current participants:"]
                for participant in items:
                    name = participant.get("name", "Unknown")
                    p_type = participant.get("type", "Unknown")
                    lines.append(f"- {name} ({p_type})")
                return ToolResult(data="\n".join(lines))
            return ToolResult(data=str(result))

        return await helpers.execute(
            context,
            "get_participants",
            "getting participants",
            _body,
        )

    @p.tool
    async def band_create_chatroom(
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

        async def _body(tools: Any) -> ToolResult:
            result = await tools.create_chatroom(task_id if task_id else None)
            logger.info("[Parlant Tool] Created chatroom: %s", result)
            return ToolResult(data=f"Created new chat room: {result}")

        return await helpers.execute(
            context,
            "create_chatroom",
            "creating chatroom",
            _body,
        )

    return [
        band_send_message,
        band_send_event,
        band_add_participant,
        band_remove_participant,
        band_lookup_peers,
        band_get_participants,
        band_create_chatroom,
    ]
