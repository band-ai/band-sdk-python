"""Built-in framework adapters.

Adapters are lazily imported to avoid requiring all optional dependencies.
Install the extra you need::

    uv add band-sdk[langgraph]
    uv add band-sdk[anthropic]
    uv add band-sdk[pydantic_ai]
    uv add band-sdk[claude_sdk]
    uv add band-sdk[parlant]
    uv add band-sdk[crewai]
    uv add band-sdk[gemini]
    uv add band-sdk[a2a]
    uv add band-sdk[a2a_gateway]
    uv add band-sdk[codex]
    uv add band-sdk[google_adk]
    uv add band-sdk[opencode]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Type-only imports for static analysis (pyrefly, mypy, etc.)
if TYPE_CHECKING:
    from thenvoi.adapters.langgraph import LangGraphAdapter as LangGraphAdapter
    from thenvoi.adapters.anthropic import AnthropicAdapter as AnthropicAdapter
    from thenvoi.adapters.pydantic_ai import PydanticAIAdapter as PydanticAIAdapter
    from thenvoi.adapters.claude_sdk import ClaudeSDKAdapter as ClaudeSDKAdapter
    from thenvoi.adapters.parlant import ParlantAdapter as ParlantAdapter
    from thenvoi.adapters.crewai import CrewAIAdapter as CrewAIAdapter
    from thenvoi.adapters.crewai_flow import (
        CrewAIFlowAdapter as CrewAIFlowAdapter,
    )
    from thenvoi.adapters.a2a import A2AAdapter as A2AAdapter
    from thenvoi.adapters.a2a_gateway import A2AGatewayAdapter as A2AGatewayAdapter
    from thenvoi.adapters.codex import CodexAdapter as CodexAdapter
    from thenvoi.adapters.codex import CodexAdapterConfig as CodexAdapterConfig
    from thenvoi.adapters.acp import (
        ACPClientAdapter as ACPClientAdapter,
        ACPServer as ACPServer,
        BandACPServerAdapter as BandACPServerAdapter,
    )
    from thenvoi.adapters.gemini import GeminiAdapter as GeminiAdapter
    from thenvoi.adapters.google_adk import GoogleADKAdapter as GoogleADKAdapter
    from thenvoi.adapters.opencode import OpencodeAdapter as OpencodeAdapter
    from thenvoi.adapters.opencode import OpencodeAdapterConfig as OpencodeAdapterConfig
    from thenvoi.adapters.letta import LettaAdapter as LettaAdapter
    from thenvoi.adapters.letta import LettaAdapterConfig as LettaAdapterConfig

__all__ = [
    "LangGraphAdapter",
    "AnthropicAdapter",
    "PydanticAIAdapter",
    "ClaudeSDKAdapter",
    "ParlantAdapter",
    "CrewAIAdapter",
    "CrewAIFlowAdapter",
    "A2AAdapter",
    "A2AGatewayAdapter",
    "CodexAdapter",
    "CodexAdapterConfig",
    "ACPClientAdapter",
    "ACPServer",
    "BandACPServerAdapter",
    "GeminiAdapter",
    "GoogleADKAdapter",
    "OpencodeAdapter",
    "OpencodeAdapterConfig",
    "LettaAdapter",
    "LettaAdapterConfig",
]


def __getattr__(name: str) -> type:
    """Lazy import adapters to avoid loading optional dependencies."""
    if name == "LangGraphAdapter":
        from thenvoi.adapters.langgraph import LangGraphAdapter

        return LangGraphAdapter
    elif name == "AnthropicAdapter":
        from thenvoi.adapters.anthropic import AnthropicAdapter

        return AnthropicAdapter
    elif name == "PydanticAIAdapter":
        from thenvoi.adapters.pydantic_ai import PydanticAIAdapter

        return PydanticAIAdapter
    elif name == "ClaudeSDKAdapter":
        from thenvoi.adapters.claude_sdk import ClaudeSDKAdapter

        return ClaudeSDKAdapter
    elif name == "ParlantAdapter":
        from thenvoi.adapters.parlant import ParlantAdapter

        return ParlantAdapter
    elif name == "CrewAIAdapter":
        from thenvoi.adapters.crewai import CrewAIAdapter

        return CrewAIAdapter
    elif name == "CrewAIFlowAdapter":
        from thenvoi.adapters.crewai_flow import CrewAIFlowAdapter

        return CrewAIFlowAdapter
    elif name == "A2AAdapter":
        from thenvoi.adapters.a2a import A2AAdapter

        return A2AAdapter
    elif name == "A2AGatewayAdapter":
        from thenvoi.adapters.a2a_gateway import A2AGatewayAdapter

        return A2AGatewayAdapter
    elif name == "CodexAdapter":
        from thenvoi.adapters.codex import CodexAdapter

        return CodexAdapter
    elif name == "CodexAdapterConfig":
        from thenvoi.adapters.codex import CodexAdapterConfig

        return CodexAdapterConfig
    elif name in ("ACPClientAdapter", "ACPServer", "BandACPServerAdapter"):
        from thenvoi.adapters.acp import (
            ACPClientAdapter,
            ACPServer,
            BandACPServerAdapter,
        )

        if name == "ACPClientAdapter":
            return ACPClientAdapter
        elif name == "ACPServer":
            return ACPServer
        return BandACPServerAdapter
    elif name == "GeminiAdapter":
        from thenvoi.adapters.gemini import GeminiAdapter

        return GeminiAdapter
    elif name == "GoogleADKAdapter":
        from thenvoi.adapters.google_adk import GoogleADKAdapter

        return GoogleADKAdapter
    elif name == "OpencodeAdapter":
        from thenvoi.adapters.opencode import OpencodeAdapter

        return OpencodeAdapter
    elif name == "OpencodeAdapterConfig":
        from thenvoi.adapters.opencode import OpencodeAdapterConfig

        return OpencodeAdapterConfig
    elif name == "LettaAdapter":
        from thenvoi.adapters.letta import LettaAdapter

        return LettaAdapter
    elif name == "LettaAdapterConfig":
        from thenvoi.adapters.letta import LettaAdapterConfig

        return LettaAdapterConfig
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
