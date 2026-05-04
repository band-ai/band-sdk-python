"""
Convert AgentTools to LangChain StructuredTool format.

This module provides the bridge between the SDK's AgentTools and LangChain's
StructuredTool format for use with LangGraph.
"""

from typing import Any, Literal

from langchain_core.tools import StructuredTool

from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.runtime.tools import get_tool_description


def agent_tools_to_langchain(
    tools: AgentToolsProtocol,
    *,
    include_memory_tools: bool = False,
    include_contacts: bool = True,
) -> list[Any]:
    """
    Convert AgentTools to LangChain StructuredTool instances.

    Args:
        tools: AgentTools instance bound to a room
        include_memory_tools: If True, include memory tools (enterprise only)

    Returns:
        List of LangChain StructuredTool instances
    """

    # Create wrapper functions that capture the tools instance
    # All wrappers catch exceptions and return error strings so LLM can see failures
    async def send_message_wrapper(
        content: str, mentions: list[str]
    ) -> dict[str, Any] | str:
        """Send a message to the chat room. Provide participant handles in mentions (e.g., '@john', '@john/weather-agent')."""
        try:
            return await tools.send_message(content, mentions)
        except Exception as e:
            return f"Error sending message: {e}"

    async def add_participant_wrapper(
        identifier: str, role: str = "member"
    ) -> dict[str, Any] | str:
        """Add a participant (agent or user) to the chat room. Use thenvoi_lookup_peers first to find available agents. Accepts a handle, name, or ID."""
        try:
            return await tools.add_participant(identifier, role)
        except Exception as e:
            return f"Error adding participant '{identifier}': {e}"

    async def remove_participant_wrapper(identifier: str) -> dict[str, Any] | str:
        """Remove a participant from the chat room. Accepts a handle, name, or ID."""
        try:
            return await tools.remove_participant(identifier)
        except Exception as e:
            return f"Error removing participant '{identifier}': {e}"

    async def lookup_peers_wrapper(
        page: int = 1, page_size: int = 50
    ) -> dict[str, Any] | str:
        """List available peers (agents and users) on the platform. Returns paginated results with metadata."""
        try:
            return await tools.lookup_peers(page, page_size)
        except Exception as e:
            return f"Error looking up peers: {e}"

    async def get_participants_wrapper() -> list[dict[str, Any]] | str:
        """Get participants in the chat room."""
        try:
            return await tools.get_participants()
        except Exception as e:
            return f"Error getting participants: {e}"

    async def create_chatroom_wrapper(task_id: str | None = None) -> str:
        """Create a new chat room."""
        try:
            return await tools.create_chatroom(task_id)
        except Exception as e:
            return f"Error creating chatroom (task_id={task_id}): {e}"

    async def send_event_wrapper(
        content: str,
        message_type: Literal["thought", "error", "task"],
    ) -> dict[str, Any] | str:
        """Send an event to the chat room. No mentions required.

        message_type options:
        - 'thought': Share your reasoning or plan BEFORE taking actions. Explain what you're about to do and why.
        - 'error': Report an error or problem that occurred.
        - 'task': Report task progress or completion status.

        Always send a thought before complex actions to keep users informed.
        """
        try:
            return await tools.send_event(content, message_type, None)
        except Exception as e:
            return f"Error sending event: {e}"

    # Contact management tools
    async def list_contacts_wrapper(
        page: int = 1, page_size: int = 50
    ) -> dict[str, Any] | str:
        """List agent's contacts with pagination."""
        try:
            return await tools.list_contacts(page, page_size)
        except Exception as e:
            return f"Error listing contacts: {e}"

    async def add_contact_wrapper(
        handle: str, message: str | None = None
    ) -> dict[str, Any] | str:
        """Send a contact request to add someone as a contact."""
        try:
            return await tools.add_contact(handle, message)
        except Exception as e:
            return f"Error adding contact '{handle}': {e}"

    async def remove_contact_wrapper(
        handle: str | None = None, contact_id: str | None = None
    ) -> dict[str, Any] | str:
        """Remove an existing contact by handle or ID."""
        try:
            return await tools.remove_contact(handle, contact_id)
        except Exception as e:
            return f"Error removing contact: {e}"

    async def list_contact_requests_wrapper(
        page: int = 1, page_size: int = 50, sent_status: str = "pending"
    ) -> dict[str, Any] | str:
        """List both received and sent contact requests."""
        try:
            return await tools.list_contact_requests(page, page_size, sent_status)
        except Exception as e:
            return f"Error listing contact requests: {e}"

    async def respond_contact_request_wrapper(
        action: str, handle: str | None = None, request_id: str | None = None
    ) -> dict[str, Any] | str:
        """Respond to a contact request (approve, reject, or cancel)."""
        try:
            return await tools.respond_contact_request(action, handle, request_id)
        except Exception as e:
            return f"Error responding to contact request: {e}"

    # Memory management tools
    async def list_memories_wrapper(
        subject_id: str | None = None,
        scope: str | None = None,
        system: str | None = None,
        type: str | None = None,
        segment: str | None = None,
        content_query: str | None = None,
        page_size: int = 50,
        status: str | None = None,
    ) -> dict[str, Any] | str:
        """List memories accessible to the agent."""
        try:
            return await tools.list_memories(
                subject_id=subject_id,
                scope=scope,
                system=system,
                type=type,
                segment=segment,
                content_query=content_query,
                page_size=page_size,
                status=status,
            )
        except Exception as e:
            return f"Error listing memories: {e}"

    async def store_memory_wrapper(
        content: str,
        system: str,
        type: str,
        segment: str,
        thought: str,
        scope: str = "subject",
        subject_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | str:
        """Store a new memory entry."""
        try:
            return await tools.store_memory(
                content=content,
                system=system,
                type=type,
                segment=segment,
                thought=thought,
                scope=scope,
                subject_id=subject_id,
                metadata=metadata,
            )
        except Exception as e:
            return f"Error storing memory: {e}"

    async def get_memory_wrapper(memory_id: str) -> dict[str, Any] | str:
        """Retrieve a specific memory by ID."""
        try:
            return await tools.get_memory(memory_id)
        except Exception as e:
            return f"Error getting memory: {e}"

    async def supersede_memory_wrapper(memory_id: str) -> dict[str, Any] | str:
        """Mark a memory as superseded (soft delete)."""
        try:
            return await tools.supersede_memory(memory_id)
        except Exception as e:
            return f"Error superseding memory: {e}"

    async def archive_memory_wrapper(memory_id: str) -> dict[str, Any] | str:
        """Archive a memory (hide but preserve)."""
        try:
            return await tools.archive_memory(memory_id)
        except Exception as e:
            return f"Error archiving memory: {e}"

    # Base platform tools (always included)
    platform_tools = [
        StructuredTool.from_function(
            coroutine=send_message_wrapper,
            name="thenvoi_send_message",
            description=get_tool_description("thenvoi_send_message"),
        ),
        StructuredTool.from_function(
            coroutine=add_participant_wrapper,
            name="thenvoi_add_participant",
            description=get_tool_description("thenvoi_add_participant"),
        ),
        StructuredTool.from_function(
            coroutine=remove_participant_wrapper,
            name="thenvoi_remove_participant",
            description=get_tool_description("thenvoi_remove_participant"),
        ),
        StructuredTool.from_function(
            coroutine=lookup_peers_wrapper,
            name="thenvoi_lookup_peers",
            description=get_tool_description("thenvoi_lookup_peers"),
        ),
        StructuredTool.from_function(
            coroutine=get_participants_wrapper,
            name="thenvoi_get_participants",
            description=get_tool_description("thenvoi_get_participants"),
        ),
        StructuredTool.from_function(
            coroutine=create_chatroom_wrapper,
            name="thenvoi_create_chatroom",
            description=get_tool_description("thenvoi_create_chatroom"),
        ),
        StructuredTool.from_function(
            coroutine=send_event_wrapper,
            name="thenvoi_send_event",
            description=get_tool_description("thenvoi_send_event"),
        ),
    ]

    # Contact management tools (opt-in via Capability.CONTACTS)
    if include_contacts:
        platform_tools.extend(
            [
                StructuredTool.from_function(
                    coroutine=list_contacts_wrapper,
                    name="thenvoi_list_contacts",
                    description=get_tool_description("thenvoi_list_contacts"),
                ),
                StructuredTool.from_function(
                    coroutine=add_contact_wrapper,
                    name="thenvoi_add_contact",
                    description=get_tool_description("thenvoi_add_contact"),
                ),
                StructuredTool.from_function(
                    coroutine=remove_contact_wrapper,
                    name="thenvoi_remove_contact",
                    description=get_tool_description("thenvoi_remove_contact"),
                ),
                StructuredTool.from_function(
                    coroutine=list_contact_requests_wrapper,
                    name="thenvoi_list_contact_requests",
                    description=get_tool_description("thenvoi_list_contact_requests"),
                ),
                StructuredTool.from_function(
                    coroutine=respond_contact_request_wrapper,
                    name="thenvoi_respond_contact_request",
                    description=get_tool_description("thenvoi_respond_contact_request"),
                ),
            ]
        )

    # Memory tools (enterprise only - opt-in)
    if include_memory_tools:
        platform_tools.extend(
            [
                StructuredTool.from_function(
                    coroutine=list_memories_wrapper,
                    name="thenvoi_list_memories",
                    description=get_tool_description("thenvoi_list_memories"),
                ),
                StructuredTool.from_function(
                    coroutine=store_memory_wrapper,
                    name="thenvoi_store_memory",
                    description=get_tool_description("thenvoi_store_memory"),
                ),
                StructuredTool.from_function(
                    coroutine=get_memory_wrapper,
                    name="thenvoi_get_memory",
                    description=get_tool_description("thenvoi_get_memory"),
                ),
                StructuredTool.from_function(
                    coroutine=supersede_memory_wrapper,
                    name="thenvoi_supersede_memory",
                    description=get_tool_description("thenvoi_supersede_memory"),
                ),
                StructuredTool.from_function(
                    coroutine=archive_memory_wrapper,
                    name="thenvoi_archive_memory",
                    description=get_tool_description("thenvoi_archive_memory"),
                ),
            ]
        )

    return platform_tools
