"""
Convert AgentTools to LangChain StructuredTool format.

This module provides the bridge between the SDK's AgentTools registry and
LangChain's StructuredTool format for use with LangGraph.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.runtime.tools import get_tool_description, iter_tool_definitions


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
        include_contacts: If True, include contact-management tools. Hub-room
            tools force this on because the hub-room prompt can ask for contact
            management regardless of the adapter's normal capability gate.

    Returns:
        List of LangChain StructuredTool instances
    """
    effective_include_contacts = include_contacts or bool(
        getattr(tools, "is_hub_room", False)
    )

    platform_tools: list[Any] = []
    for definition in iter_tool_definitions(
        include_memory=include_memory_tools,
        include_contacts=effective_include_contacts,
    ):
        description = definition.input_model.__doc__ or get_tool_description(
            definition.name
        )

        async def execute_definition(
            *,
            _tool_name: str = definition.name,
            **kwargs: Any,
        ) -> Any:
            try:
                return await tools.execute_tool_call(_tool_name, kwargs)
            except Exception as e:
                return f"Error executing {_tool_name}: {e}"

        execute_definition.__name__ = f"{definition.method_name}_wrapper"
        execute_definition.__doc__ = description

        platform_tools.append(
            StructuredTool.from_function(
                coroutine=execute_definition,
                name=definition.name,
                description=description,
                args_schema=definition.input_model,
            )
        )

    return platform_tools
