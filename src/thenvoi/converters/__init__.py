"""Built-in history converters.

Converters are lazily imported to avoid requiring all optional dependencies.
Install the extra you need::

    uv add thenvoi-sdk[langgraph]
    uv add thenvoi-sdk[anthropic]
    uv add thenvoi-sdk[pydantic_ai]
    uv add thenvoi-sdk[claude_sdk]
    uv add thenvoi-sdk[parlant]
    uv add thenvoi-sdk[crewai]
    uv add thenvoi-sdk[gemini]
    uv add thenvoi-sdk[a2a]
    uv add thenvoi-sdk[codex]
    uv add thenvoi-sdk[google_adk]
    uv add thenvoi-sdk[opencode]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Type-only imports for static analysis (pyrefly, mypy, etc.)
if TYPE_CHECKING:
    from thenvoi.converters.langchain import (
        LangChainHistoryConverter as LangChainHistoryConverter,
        LangChainMessages as LangChainMessages,
    )
    from thenvoi.converters.anthropic import (
        AnthropicHistoryConverter as AnthropicHistoryConverter,
        AnthropicMessages as AnthropicMessages,
    )
    from thenvoi.converters.pydantic_ai import (
        PydanticAIHistoryConverter as PydanticAIHistoryConverter,
        PydanticAIMessages as PydanticAIMessages,
    )
    from thenvoi.converters.claude_sdk import (
        ClaudeSDKHistoryConverter as ClaudeSDKHistoryConverter,
    )
    from thenvoi.converters.parlant import (
        ParlantHistoryConverter as ParlantHistoryConverter,
        ParlantMessages as ParlantMessages,
    )
    from thenvoi.converters.crewai import (
        CrewAIHistoryConverter as CrewAIHistoryConverter,
        CrewAIMessages as CrewAIMessages,
    )
    from thenvoi.converters.crewai_flow import (
        CrewAIFlowSessionState as CrewAIFlowSessionState,
        CrewAIFlowStateConverter as CrewAIFlowStateConverter,
    )
    from thenvoi.converters.a2a import (
        A2AHistoryConverter as A2AHistoryConverter,
    )
    from thenvoi.converters.a2a_gateway import (
        GatewayHistoryConverter as GatewayHistoryConverter,
    )
    from thenvoi.converters.codex import (
        CodexHistoryConverter as CodexHistoryConverter,
    )
    from thenvoi.converters.acp_server import (
        ACPServerHistoryConverter as ACPServerHistoryConverter,
    )
    from thenvoi.converters.acp_client import (
        ACPClientHistoryConverter as ACPClientHistoryConverter,
    )
    from thenvoi.converters.gemini import (
        GeminiHistoryConverter as GeminiHistoryConverter,
        GeminiMessages as GeminiMessages,
    )
    from thenvoi.converters.google_adk import (
        GoogleADKHistoryConverter as GoogleADKHistoryConverter,
        GoogleADKMessages as GoogleADKMessages,
    )
    from thenvoi.converters.opencode import (
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
        from thenvoi.converters.langchain import (
            LangChainHistoryConverter,
            LangChainMessages,
        )

        if name == "LangChainHistoryConverter":
            return LangChainHistoryConverter
        return LangChainMessages

    elif name in ("AnthropicHistoryConverter", "AnthropicMessages"):
        from thenvoi.converters.anthropic import (
            AnthropicHistoryConverter,
            AnthropicMessages,
        )

        if name == "AnthropicHistoryConverter":
            return AnthropicHistoryConverter
        return AnthropicMessages

    elif name in ("PydanticAIHistoryConverter", "PydanticAIMessages"):
        from thenvoi.converters.pydantic_ai import (
            PydanticAIHistoryConverter,
            PydanticAIMessages,
        )

        if name == "PydanticAIHistoryConverter":
            return PydanticAIHistoryConverter
        return PydanticAIMessages

    elif name == "ClaudeSDKHistoryConverter":
        from thenvoi.converters.claude_sdk import ClaudeSDKHistoryConverter

        return ClaudeSDKHistoryConverter

    elif name in ("ParlantHistoryConverter", "ParlantMessages"):
        from thenvoi.converters.parlant import (
            ParlantHistoryConverter,
            ParlantMessages,
        )

        if name == "ParlantHistoryConverter":
            return ParlantHistoryConverter
        return ParlantMessages

    elif name in ("CrewAIHistoryConverter", "CrewAIMessages"):
        from thenvoi.converters.crewai import (
            CrewAIHistoryConverter,
            CrewAIMessages,
        )

        if name == "CrewAIHistoryConverter":
            return CrewAIHistoryConverter
        return CrewAIMessages

    elif name in ("CrewAIFlowStateConverter", "CrewAIFlowSessionState"):
        from thenvoi.converters.crewai_flow import (
            CrewAIFlowSessionState,
            CrewAIFlowStateConverter,
        )

        if name == "CrewAIFlowStateConverter":
            return CrewAIFlowStateConverter
        return CrewAIFlowSessionState

    elif name == "A2AHistoryConverter":
        from thenvoi.converters.a2a import A2AHistoryConverter

        return A2AHistoryConverter

    elif name == "GatewayHistoryConverter":
        from thenvoi.converters.a2a_gateway import GatewayHistoryConverter

        return GatewayHistoryConverter
    elif name == "CodexHistoryConverter":
        from thenvoi.converters.codex import CodexHistoryConverter

        return CodexHistoryConverter
    elif name in ("GeminiHistoryConverter", "GeminiMessages"):
        from thenvoi.converters.gemini import GeminiHistoryConverter, GeminiMessages

        if name == "GeminiHistoryConverter":
            return GeminiHistoryConverter
        return GeminiMessages

    elif name == "ACPServerHistoryConverter":
        from thenvoi.converters.acp_server import ACPServerHistoryConverter

        return ACPServerHistoryConverter

    elif name == "ACPClientHistoryConverter":
        from thenvoi.converters.acp_client import ACPClientHistoryConverter

        return ACPClientHistoryConverter

    elif name in ("GoogleADKHistoryConverter", "GoogleADKMessages"):
        from thenvoi.converters.google_adk import (
            GoogleADKHistoryConverter,
            GoogleADKMessages,
        )

        if name == "GoogleADKHistoryConverter":
            return GoogleADKHistoryConverter
        return GoogleADKMessages
    elif name == "OpencodeHistoryConverter":
        from thenvoi.converters.opencode import OpencodeHistoryConverter

        return OpencodeHistoryConverter

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
