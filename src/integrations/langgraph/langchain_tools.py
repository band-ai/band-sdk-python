"""
Convert AgentTools to LangChain StructuredTool format.

This module provides the bridge between the SDK's AgentTools registry and
LangChain's StructuredTool format for use with LangGraph.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

from langchain_core.tools import StructuredTool

from band.core.protocols import AgentToolsProtocol
from band.core.tool_filter import filter_tool_schemas
from band.core.types import AdapterFeatures, Capability
from band.runtime.tools import (
    CHAT_TOOL_NAMES,
    CONTACT_TOOL_NAMES,
    MEMORY_TOOL_NAMES,
    format_tool_validation_error,
    get_tool_description,
    iter_tool_definitions,
)

logger = logging.getLogger(__name__)


_TOOL_CATEGORIES: dict[str, str] = {
    **{name: "chat" for name in CHAT_TOOL_NAMES},
    **{name: "contacts" for name in CONTACT_TOOL_NAMES},
    **{name: "memory" for name in MEMORY_TOOL_NAMES},
}


def get_langgraph_tool_category(name: str) -> str | None:
    """Return the AdapterFeatures category for a LangGraph platform tool."""
    return _TOOL_CATEGORIES.get(name)


def agent_tools_to_langchain(
    tools: AgentToolsProtocol,
    *,
    features: AdapterFeatures | None = None,
    include_memory_tools: bool | None = None,
    include_contacts: bool | None = None,
) -> list[Any]:
    """
    Convert AgentTools to LangChain StructuredTool instances.

    Args:
        tools: AgentTools instance bound to a room.
        features: Adapter feature config. This is the primary path for capability
            gates and framework-facing tool filters.
        include_memory_tools: Deprecated compatibility override. Use
            ``features=AdapterFeatures(capabilities={Capability.MEMORY})``.
        include_contacts: Deprecated compatibility override. Use
            ``features=AdapterFeatures(capabilities={Capability.CONTACTS})``.
            Hub-room tools still force contacts on because the hub-room prompt can
            ask for contact management regardless of the adapter's normal gate.

    Returns:
        List of LangChain StructuredTool instances.
    """
    features = features or AdapterFeatures()

    include_memory = Capability.MEMORY in features.capabilities
    include_contact_tools = Capability.CONTACTS in features.capabilities

    if include_memory_tools is not None:
        warnings.warn(
            "include_memory_tools is deprecated. Use features=AdapterFeatures(...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        include_memory = include_memory_tools

    if include_contacts is not None:
        warnings.warn(
            "include_contacts is deprecated. Use features=AdapterFeatures(...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        include_contact_tools = include_contacts

    effective_include_contacts = include_contact_tools or (
        getattr(tools, "is_hub_room", False) is True
    )

    definitions = iter_tool_definitions(
        include_memory=include_memory,
        include_contacts=effective_include_contacts,
    )
    definitions = filter_tool_schemas(
        definitions,
        features,
        get_name=lambda definition: definition.name,
        get_category=lambda definition: get_langgraph_tool_category(definition.name),
    )

    platform_tools: list[Any] = []
    for definition in definitions:
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
            except Exception:
                # Tool errors feed back into the LLM transcript and may be
                # relayed to chat. Keep the message generic; the full
                # traceback (with paths, DB strings, tokens, etc.) only
                # lives in the agent log.
                logger.exception("Error executing platform tool %s", _tool_name)
                return f"Error executing {_tool_name}: see agent logs."

        def validation_error_message(
            error: Any,
            *,
            _tool_name: str = definition.name,
        ) -> str:
            return format_tool_validation_error(_tool_name, error)

        execute_definition.__name__ = f"{definition.method_name}_wrapper"
        execute_definition.__doc__ = description

        platform_tools.append(
            StructuredTool.from_function(
                coroutine=execute_definition,
                name=definition.name,
                description=description,
                args_schema=definition.input_model,
                handle_validation_error=validation_error_message,
            )
        )

    return platform_tools
