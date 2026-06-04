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
    "GeminiHistoryConverter",
    "GeminiMessages",
    "GoogleADKHistoryConverter",
    "GoogleADKMessages",
    "OpencodeHistoryConverter",
]


def __getattr__(name: str) -> type:
    """Lazy import converters to avoid loading optional dependencies."""
    if name in ("LangChainHistoryConverter", "LangChainMessages"):
        from band.converters.langchain import (
            LangChainHistoryConverter,
            LangChainMessages,
        )

        if name == "LangChainHistoryConverter":
            return LangChainHistoryConverter
        return LangChainMessages

    elif name in ("AnthropicHistoryConverter", "AnthropicMessages"):
        from band.converters.anthropic import (
            AnthropicHistoryConverter,
            AnthropicMessages,
        )

        if name == "AnthropicHistoryConverter":
            return AnthropicHistoryConverter
        return AnthropicMessages

    elif name in ("PydanticAIHistoryConverter", "PydanticAIMessages"):
        from band.converters.pydantic_ai import (
            PydanticAIHistoryConverter,
            PydanticAIMessages,
        )

        if name == "PydanticAIHistoryConverter":
            return PydanticAIHistoryConverter
        return PydanticAIMessages

    elif name == "ClaudeSDKHistoryConverter":
        from band.converters.claude_sdk import ClaudeSDKHistoryConverter

        return ClaudeSDKHistoryConverter

    elif name in ("ParlantHistoryConverter", "ParlantMessages"):
        from band.converters.parlant import (
            ParlantHistoryConverter,
            ParlantMessages,
        )

        if name == "ParlantHistoryConverter":
            return ParlantHistoryConverter
        return ParlantMessages

    elif name in ("CrewAIHistoryConverter", "CrewAIMessages"):
        from band.converters.crewai import (
            CrewAIHistoryConverter,
            CrewAIMessages,
        )

        if name == "CrewAIHistoryConverter":
            return CrewAIHistoryConverter
        return CrewAIMessages

    elif name in ("CrewAIFlowStateConverter", "CrewAIFlowSessionState"):
        from band.converters.crewai_flow import (
            CrewAIFlowSessionState,
            CrewAIFlowStateConverter,
        )

        if name == "CrewAIFlowStateConverter":
            return CrewAIFlowStateConverter
        return CrewAIFlowSessionState

    elif name == "A2AHistoryConverter":
        from band.converters.a2a import A2AHistoryConverter

        return A2AHistoryConverter

    elif name == "GatewayHistoryConverter":
        from band.converters.a2a_gateway import GatewayHistoryConverter

        return GatewayHistoryConverter
    elif name == "CodexHistoryConverter":
        from band.converters.codex import CodexHistoryConverter

        return CodexHistoryConverter
    elif name in ("GeminiHistoryConverter", "GeminiMessages"):
        from band.converters.gemini import GeminiHistoryConverter, GeminiMessages

        if name == "GeminiHistoryConverter":
            return GeminiHistoryConverter
        return GeminiMessages

    elif name == "ACPServerHistoryConverter":
        from band.converters.acp_server import ACPServerHistoryConverter

        return ACPServerHistoryConverter

    elif name == "ACPClientHistoryConverter":
        from band.converters.acp_client import ACPClientHistoryConverter

        return ACPClientHistoryConverter

    elif name in ("GoogleADKHistoryConverter", "GoogleADKMessages"):
        from band.converters.google_adk import (
            GoogleADKHistoryConverter,
            GoogleADKMessages,
        )

        if name == "GoogleADKHistoryConverter":
            return GoogleADKHistoryConverter
        return GoogleADKMessages
    elif name == "OpencodeHistoryConverter":
        from band.converters.opencode import OpencodeHistoryConverter

        return OpencodeHistoryConverter

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
