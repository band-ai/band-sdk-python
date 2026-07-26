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

from typing import TYPE_CHECKING

from band.exports import lazy_exports

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

__all__, __getattr__, __dir__ = lazy_exports(
    __name__,
    {
        "langchain": ("LangChainHistoryConverter", "LangChainMessages"),
        "anthropic": ("AnthropicHistoryConverter", "AnthropicMessages"),
        "pydantic_ai": ("PydanticAIHistoryConverter", "PydanticAIMessages"),
        "claude_sdk": ("ClaudeSDKHistoryConverter",),
        "copilot_sdk": ("CopilotSDKHistoryConverter", "CopilotSDKSessionState"),
        "parlant": ("ParlantHistoryConverter", "ParlantMessages"),
        "crewai": ("CrewAIHistoryConverter", "CrewAIMessages"),
        "crewai_flow": ("CrewAIFlowStateConverter", "CrewAIFlowSessionState"),
        "a2a": ("A2AHistoryConverter",),
        "a2a_gateway": ("GatewayHistoryConverter",),
        "codex": ("CodexHistoryConverter",),
        "acp_server": ("ACPServerHistoryConverter",),
        "acp_client": ("ACPClientHistoryConverter",),
        "agno": ("AgnoHistoryConverter", "AgnoMessages"),
        "gemini": ("GeminiHistoryConverter", "GeminiMessages"),
        "google_adk": ("GoogleADKHistoryConverter", "GoogleADKMessages"),
        "opencode": ("OpencodeHistoryConverter",),
    },
)
