"""Memory-management Parlant tools.

NOTE: Do NOT add ``from __future__ import annotations`` to this module. The
``@p.tool`` decorator inspects parameter annotations at runtime, so postponed
annotations would turn ``ToolContext``/``ToolResult`` into unresolvable strings
and break tool registration.
"""

import json
import logging
from typing import Any

from band.core.memory_types import (
    MemorySegment,
    MemoryStoreScope,
    MemorySystem,
    enum_values,
    memory_type_field_description,
)

logger = logging.getLogger(__name__)


def build_memory_tools(
    p: Any,
    ToolContext: Any,
    ToolResult: Any,
    helpers: Any,
) -> list[Any]:
    """Build memory-management Parlant tools."""

    @p.tool
    async def band_list_memories(
        context: ToolContext,
        content_query: str = "",
        scope: str = "",
        system: str = "",
        memory_type: str = "",
        segment: str = "",
        status: str = "",
        page_size: int = 50,
    ) -> ToolResult:
        """
        List memories accessible to the agent.

        Args:
            context: Parlant tool context (automatically provided)
            content_query: Optional full-text search query
            scope: Optional scope filter: 'subject', 'organization', or 'all'
            system: Optional memory system: 'sensory', 'working', or 'long_term'
            memory_type: Optional memory type such as 'semantic' or 'procedural'
            segment: Optional segment: 'user', 'agent', 'tool', or 'guideline'
            status: Optional status: 'active', 'superseded', 'archived', or 'all'
            page_size: Number of memories to return (default 50)

        Returns:
            JSON with memories and pagination metadata
        """
        logger.info(
            "[Parlant Tool] list_memories called: session=%s, query=%s",
            context.session_id,
            content_query,
        )

        async def _body(tools: Any) -> ToolResult:
            list_kwargs: dict[str, Any] = {"page_size": page_size}
            optional_filters = {
                "content_query": content_query,
                "scope": scope,
                "system": system,
                "type": memory_type,
                "segment": segment,
                "status": status,
            }
            list_kwargs.update(
                {key: value for key, value in optional_filters.items() if value}
            )
            result = await tools.list_memories(**list_kwargs)
            return helpers.json_result(result)

        return await helpers.execute(
            context,
            "list_memories",
            "listing memories",
            _body,
        )

    @p.tool
    async def band_store_memory(
        context: ToolContext,
        content: str = "",
        system: str = "",
        memory_type: str = "",
        segment: str = "",
        thought: str = "",
        scope: str = "",
        subject_id: str = "",
        metadata: str = "",
    ) -> ToolResult:
        """
        Store durable information in Band memory.

        All of content, system, memory_type, segment, thought, and scope are
        required; if any is missing the tool returns guidance listing the valid
        choices. Value validation (system/type pairing, subject scope) is handled
        by the platform and surfaced as an error you can act on.

        Args:
            context: Parlant tool context (automatically provided)
            content: The durable memory content to store
            system: Memory system: 'sensory', 'working', or 'long_term'
            memory_type: Memory type that must match the system: 'sensory' uses
                'iconic'/'echoic'/'haptic'; 'working'/'long_term' use
                'episodic'/'semantic'/'procedural'
            segment: Segment: 'user', 'agent', 'tool', or 'guideline'
            thought: Brief reason why this information is durable
            scope: Visibility scope: 'subject' or 'organization'
            subject_id: Required only when scope is 'subject'; omit otherwise
            metadata: Optional JSON object string with extra metadata

        Returns:
            JSON for the stored memory or an error message
        """
        logger.info(
            "[Parlant Tool] store_memory called: session=%s, system=%s, type=%s, scope=%s",
            context.session_id,
            system,
            memory_type,
            scope,
        )

        async def _body(tools: Any) -> ToolResult:
            required_hints = {
                "content": "content (the text to remember)",
                "system": f"system (one of: {', '.join(enum_values(MemorySystem))})",
                "memory_type": memory_type_field_description(),
                "segment": f"segment (one of: {', '.join(enum_values(MemorySegment))})",
                "thought": "thought (brief reason this information is durable)",
                "scope": f"scope (one of: {', '.join(enum_values(MemoryStoreScope))})",
            }
            provided = {
                "content": content,
                "system": system,
                "memory_type": memory_type,
                "segment": segment,
                "thought": thought,
                "scope": scope,
            }
            missing = [
                hint for field, hint in required_hints.items() if not provided[field]
            ]
            if missing:
                return ToolResult(
                    data="Error: band_store_memory needs: " + "; ".join(missing) + "."
                )

            store_kwargs: dict[str, Any] = {
                "content": content,
                "system": system,
                "type": memory_type,
                "segment": segment,
                "thought": thought,
                "scope": scope,
            }
            if subject_id:
                store_kwargs["subject_id"] = subject_id
            if metadata:
                try:
                    metadata_data = json.loads(metadata)
                except json.JSONDecodeError as e:
                    return ToolResult(data=f"Error: metadata must be valid JSON: {e}")
                if not isinstance(metadata_data, dict):
                    return ToolResult(data="Error: metadata must be a JSON object")
                store_kwargs["metadata"] = metadata_data

            result = await tools.store_memory(**store_kwargs)
            return helpers.json_result(result)

        return await helpers.execute(
            context,
            "store_memory",
            "storing memory",
            _body,
        )

    @p.tool
    async def band_get_memory(
        context: ToolContext,
        memory_id: str = "",
    ) -> ToolResult:
        """
        Retrieve a specific Band memory by ID.

        Only use this when you already have a memory_id from band_list_memories.

        Args:
            context: Parlant tool context (automatically provided)
            memory_id: Memory ID (UUID)

        Returns:
            JSON for the memory or an error message
        """
        logger.info(
            "[Parlant Tool] get_memory called: session=%s, memory_id=%s",
            context.session_id,
            memory_id,
        )

        async def _body(tools: Any) -> ToolResult:
            missing = helpers.require_memory_id(memory_id)
            if missing is not None:
                return missing
            result = await tools.get_memory(memory_id)
            return helpers.json_result(result)

        return await helpers.execute(context, "get_memory", "getting memory", _body)

    @p.tool
    async def band_supersede_memory(
        context: ToolContext,
        memory_id: str = "",
    ) -> ToolResult:
        """
        Mark a Band memory as superseded when it is outdated.

        Only use this when you already have a memory_id from band_list_memories.

        Args:
            context: Parlant tool context (automatically provided)
            memory_id: Memory ID (UUID)

        Returns:
            JSON for the superseded memory or an error message
        """
        logger.info(
            "[Parlant Tool] supersede_memory called: session=%s, memory_id=%s",
            context.session_id,
            memory_id,
        )

        async def _body(tools: Any) -> ToolResult:
            missing = helpers.require_memory_id(memory_id)
            if missing is not None:
                return missing
            result = await tools.supersede_memory(memory_id)
            return helpers.json_result(result)

        return await helpers.execute(
            context,
            "supersede_memory",
            "superseding memory",
            _body,
        )

    @p.tool
    async def band_archive_memory(
        context: ToolContext,
        memory_id: str = "",
    ) -> ToolResult:
        """
        Archive a Band memory that should be hidden but preserved.

        Only use this when you already have a memory_id from band_list_memories.

        Args:
            context: Parlant tool context (automatically provided)
            memory_id: Memory ID (UUID)

        Returns:
            JSON for the archived memory or an error message
        """
        logger.info(
            "[Parlant Tool] archive_memory called: session=%s, memory_id=%s",
            context.session_id,
            memory_id,
        )

        async def _body(tools: Any) -> ToolResult:
            missing = helpers.require_memory_id(memory_id)
            if missing is not None:
                return missing
            result = await tools.archive_memory(memory_id)
            return helpers.json_result(result)

        return await helpers.execute(
            context,
            "archive_memory",
            "archiving memory",
            _body,
        )

    return [
        band_list_memories,
        band_store_memory,
        band_get_memory,
        band_supersede_memory,
        band_archive_memory,
    ]
