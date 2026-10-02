"""Claude SDK names for the Band MCP tools.

The tools themselves are served by the shared Band MCP backend
(``band.integrations.mcp``).
"""

from __future__ import annotations

import warnings
from collections.abc import Awaitable, Callable
from typing import Any

from band.core.protocols import AgentToolsProtocol
from band.runtime.tools import BASE_TOOL_NAMES, CHAT_TOOL_NAMES, mcp_tool_names

# Tool names as constants (MCP naming convention: mcp__{server}__{tool})
BAND_CHAT_TOOLS: list[str] = mcp_tool_names(CHAT_TOOL_NAMES)
BAND_BASE_TOOLS: list[str] = mcp_tool_names(BASE_TOOL_NAMES)

_BAND_TOOLS: list[str] = BAND_CHAT_TOOLS

ToolResolver = Callable[[str], AgentToolsProtocol | None]
ParticipantHandlesResolver = Callable[[str], list[str]]
ToolResultHook = Callable[[str, str, Any], Awaitable[None] | None]


def __getattr__(name: str) -> Any:
    if name == "BAND_TOOLS":
        warnings.warn(
            "BAND_TOOLS is deprecated, use BAND_CHAT_TOOLS instead. "
            f"Note: this contains only chat tools ({len(_BAND_TOOLS)}). "
            "For all tools including contacts and memory, use "
            "band.adapters.claude_sdk.BAND_ALL_TOOLS.",
            DeprecationWarning,
            stacklevel=2,
        )
        return _BAND_TOOLS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
