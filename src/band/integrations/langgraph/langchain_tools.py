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

from band.core.exceptions import BandToolError
from band.core.protocols import AgentToolsProtocol
from band.core.tool_filter import filter_tool_schemas
from band.core.types import AdapterFeatures, Capability
from band.runtime.capabilities import with_hub_room_contacts
from band.runtime.custom_tools import (
    CustomToolDef,
    execute_custom_tool,
    get_custom_tool_name,
)
from band.runtime.tools import (
    READ_ROOM_FILE_TOOL_NAME,
    format_tool_validation_error,
    get_band_tool_category,
    get_tool_description,
    is_mcp_content_result,
    iter_tool_definitions,
)

logger = logging.getLogger(__name__)


def _with_capability(
    capabilities: frozenset[Capability], capability: Capability, enabled: bool
) -> frozenset[Capability]:
    """``capabilities`` with ``capability`` forced on or off per ``enabled``."""
    return capabilities | {capability} if enabled else capabilities - {capability}


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

    capabilities = features.capabilities

    if include_memory_tools is not None:
        warnings.warn(
            "include_memory_tools is deprecated. Use features=AdapterFeatures(...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        capabilities = _with_capability(
            capabilities, Capability.MEMORY, include_memory_tools
        )

    if include_contacts is not None:
        warnings.warn(
            "include_contacts is deprecated. Use features=AdapterFeatures(...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        capabilities = _with_capability(
            capabilities, Capability.CONTACTS, include_contacts
        )

    effective_capabilities = with_hub_room_contacts(
        capabilities, is_hub_room=tools.is_hub_room
    )

    definitions = iter_tool_definitions(capabilities=effective_capabilities)
    definitions = filter_tool_schemas(
        definitions,
        features,
        get_name=lambda definition: definition.name,
        get_category=lambda definition: get_band_tool_category(definition.name),
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
                result = await tools.execute_tool_call(_tool_name, kwargs)
                if _tool_name == READ_ROOM_FILE_TOOL_NAME and is_mcp_content_result(
                    result
                ):
                    return [
                        {
                            "type": "image",
                            "mime_type": block["mimeType"],
                            "base64": block["data"],
                        }
                        for block in result["content"]
                    ]
                return result
            except (BandToolError, ValueError) as e:
                return str(e)
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


def custom_tool_defs_to_langchain(tools: list[CustomToolDef]) -> list[StructuredTool]:
    """Convert band ``CustomToolDef`` tuples to LangChain ``StructuredTool``s.

    Lets the LangGraph adapter accept the SDK's portable custom-tool form —
    ``(InputModel, handler)`` — the same shape every other adapter takes, rather
    than only ready-made LangChain tools. Each tuple becomes a StructuredTool whose
    schema is the ``InputModel`` and whose body validates + runs the handler via
    ``execute_custom_tool`` (which handles sync and async handlers alike).
    """
    langchain_tools: list[StructuredTool] = []
    for input_model, handler in tools:
        name = get_custom_tool_name(input_model)
        description = input_model.__doc__ or f"Execute {name}"

        async def execute_custom(
            *,
            _tool: CustomToolDef = (input_model, handler),
            _name: str = name,
            **kwargs: Any,
        ) -> Any:
            try:
                return await execute_custom_tool(_tool, kwargs)
            except ValueError as e:
                # Bad-argument / validation errors feed back to the LLM to retry.
                return str(e)
            except Exception:
                # Keep the message generic; the full traceback (paths, secrets)
                # only lives in the agent log — see agent_tools_to_langchain.
                logger.exception("Error executing custom tool %s", _name)
                return f"Error executing {_name}: see agent logs."

        def on_validation_error(error: Any, *, _name: str = name) -> str:
            # Schema validation runs before the coroutine; feed bad args back to the
            # LLM (to retry) via the shared formatter, so the wording matches the
            # band-tool path (agent_tools_to_langchain) exactly.
            return format_tool_validation_error(_name, error)

        execute_custom.__name__ = f"{name}_wrapper"
        execute_custom.__doc__ = description

        langchain_tools.append(
            StructuredTool.from_function(
                coroutine=execute_custom,
                name=name,
                description=description,
                args_schema=input_model,
                handle_validation_error=on_validation_error,
            )
        )

    return langchain_tools
