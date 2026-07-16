"""Built-in history converters.

Converters are lazily imported to avoid requiring all optional dependencies.
Install the extra you need::

    uv add band-sdk[langgraph]
    uv add band-sdk[anthropic]
    uv add band-sdk[pydantic_ai]
    uv add band-sdk[claude_sdk]
    uv add band-sdk[parlant]
    uv add band-sdk[crewai]
    uv add band-sdk[gemini]
    uv add band-sdk[a2a]
    uv add band-sdk[codex]
    uv add band-sdk[google_adk]
    uv add band-sdk[opencode]
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

# Type-only imports for static analysis (pyrefly, mypy, etc.)
if TYPE_CHECKING:
    from band.converters.langchain import (
        LangChainHistoryConverter as LangChainHistoryConverter,
        LangChainMessages as LangChainMessages,
    )
    from band.converters.anthropic import (
        AnthropicHistoryConverter as AnthropicHistoryConverter,
        AnthropicMessages as AnthropicMessages,
    )
    from band.converters.pydantic_ai import (
        PydanticAIHistoryConverter as PydanticAIHistoryConverter,
        PydanticAIMessages as PydanticAIMessages,
    )
    from band.converters.claude_sdk import (
        ClaudeSDKHistoryConverter as ClaudeSDKHistoryConverter,
    )
    from band.converters.copilot_sdk import (
        CopilotSDKHistoryConverter as CopilotSDKHistoryConverter,
        CopilotSDKSessionState as CopilotSDKSessionState,
    )
    from band.converters.parlant import (
        ParlantHistoryConverter as ParlantHistoryConverter,
        ParlantMessages as ParlantMessages,
    )
    from band.converters.crewai import (
        CrewAIHistoryConverter as CrewAIHistoryConverter,
        CrewAIMessages as CrewAIMessages,
    )
    from band.converters.crewai_flow import (
        CrewAIFlowSessionState as CrewAIFlowSessionState,
        CrewAIFlowStateConverter as CrewAIFlowStateConverter,
    )
    from band.converters.a2a import (
        A2AHistoryConverter as A2AHistoryConverter,
    )
    from band.converters.a2a_gateway import (
        GatewayHistoryConverter as GatewayHistoryConverter,
    )
    from band.converters.codex import (
        CodexHistoryConverter as CodexHistoryConverter,
    )
    from band.converters.acp_server import (
        ACPServerHistoryConverter as ACPServerHistoryConverter,
    )
    from band.converters.acp_client import (
        ACPClientHistoryConverter as ACPClientHistoryConverter,
    )
    from band.converters.agno import (
        AgnoHistoryConverter as AgnoHistoryConverter,
        AgnoMessages as AgnoMessages,
    )
    from band.converters.gemini import (
        GeminiHistoryConverter as GeminiHistoryConverter,
        GeminiMessages as GeminiMessages,
    )
    from band.converters.google_adk import (
        GoogleADKHistoryConverter as GoogleADKHistoryConverter,
        GoogleADKMessages as GoogleADKMessages,
    )
    from band.converters.opencode import (
        OpencodeHistoryConverter as OpencodeHistoryConverter,
    )

__all__ = [
    "LangChainHistoryConverter",
    "LangChainMessages",
    "AnthropicHistoryConverter",
    "AnthropicMessages",
    "PydanticAIHistoryConverter",
    "PydanticAIMessages",
    "ClaudeSDKHistoryConverter",
    "CopilotSDKHistoryConverter",
    "CopilotSDKSessionState",
    "ParlantHistoryConverter",
    "ParlantMessages",
    "CrewAIHistoryConverter",
    "CrewAIMessages",
    "CrewAIFlowSessionState",
    "CrewAIFlowStateConverter",
    "A2AHistoryConverter",
    "GatewayHistoryConverter",
    "CodexHistoryConverter",
    "ACPServerHistoryConverter",
    "ACPClientHistoryConverter",
    "AgnoHistoryConverter",
    "AgnoMessages",
    "GeminiHistoryConverter",
    "GeminiMessages",
    "GoogleADKHistoryConverter",
    "GoogleADKMessages",
    "OpencodeHistoryConverter",
]


# Submodule (under band.converters) providing each lazily imported name.
_LAZY_IMPORTS: dict[str, str] = {
    "LangChainHistoryConverter": "langchain",
    "LangChainMessages": "langchain",
    "AnthropicHistoryConverter": "anthropic",
    "AnthropicMessages": "anthropic",
    "PydanticAIHistoryConverter": "pydantic_ai",
    "PydanticAIMessages": "pydantic_ai",
    "ClaudeSDKHistoryConverter": "claude_sdk",
    "CopilotSDKHistoryConverter": "copilot_sdk",
    "CopilotSDKSessionState": "copilot_sdk",
    "ParlantHistoryConverter": "parlant",
    "ParlantMessages": "parlant",
    "CrewAIHistoryConverter": "crewai",
    "CrewAIMessages": "crewai",
    "CrewAIFlowStateConverter": "crewai_flow",
    "CrewAIFlowSessionState": "crewai_flow",
    "A2AHistoryConverter": "a2a",
    "GatewayHistoryConverter": "a2a_gateway",
    "CodexHistoryConverter": "codex",
    "ACPServerHistoryConverter": "acp_server",
    "ACPClientHistoryConverter": "acp_client",
    "AgnoHistoryConverter": "agno",
    "AgnoMessages": "agno",
    "GeminiHistoryConverter": "gemini",
    "GeminiMessages": "gemini",
    "GoogleADKHistoryConverter": "google_adk",
    "GoogleADKMessages": "google_adk",
    "OpencodeHistoryConverter": "opencode",
}


def __getattr__(name: str) -> type:
    """Lazy import converters to avoid loading optional dependencies."""
    module_name = _LAZY_IMPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f".{module_name}", __name__)
    return getattr(module, name)
