"""Factory that lazily imports Parlant and assembles capability-gated tools."""

from __future__ import annotations

import logging
from typing import Any

from band.core.types import AdapterFeatures, Capability

from band.integrations.parlant.tools.chat import build_chat_tools
from band.integrations.parlant.tools.contacts import build_contact_tools
from band.integrations.parlant.tools.helpers import build_helpers
from band.integrations.parlant.tools.memory import build_memory_tools

logger = logging.getLogger(__name__)


def create_parlant_tools(features: AdapterFeatures | None = None) -> list[Any]:
    """Create Parlant tool definitions that wrap Band tools.

    These tools use a session-keyed registry to access the current room's
    AgentToolsProtocol during execution.

    Args:
        features: Optional adapter features. When CONTACTS capability is absent,
            contact-management tools are excluded from the returned list. Memory
            tools are included only when MEMORY capability is present.

    Returns:
        List of Parlant ToolEntry objects
    """
    try:
        import parlant.sdk as p  # type: ignore[missing-import]
        from parlant.core.tools import ToolContext, ToolResult  # type: ignore[missing-import]
    except ImportError:
        logger.warning("Parlant SDK not installed, skipping tool creation")
        return []

    include_contacts = features is None or Capability.CONTACTS in features.capabilities
    include_memory = features is not None and Capability.MEMORY in features.capabilities
    helpers = build_helpers(ToolResult)

    tools = build_chat_tools(p, ToolContext, ToolResult, helpers)

    if include_contacts:
        tools.extend(build_contact_tools(p, ToolContext, ToolResult, helpers))

    if include_memory:
        tools.extend(build_memory_tools(p, ToolContext, ToolResult, helpers))

    return tools
