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
    uv add band-sdk[slack]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Type-only imports for static analysis (pyrefly, mypy, etc.)
if TYPE_CHECKING:
    from band.adapters.langgraph import LangGraphAdapter as LangGraphAdapter
    from band.adapters.anthropic import AnthropicAdapter as AnthropicAdapter
    from band.adapters.pydantic_ai import PydanticAIAdapter as PydanticAIAdapter
    from band.adapters.claude_sdk import ClaudeSDKAdapter as ClaudeSDKAdapter
    from band.adapters.parlant import ParlantAdapter as ParlantAdapter
    from band.adapters.crewai import CrewAIAdapter as CrewAIAdapter
    from band.adapters.crewai_flow import (
        CrewAIFlowAdapter as CrewAIFlowAdapter,
    )
    from band.adapters.a2a import A2AAdapter as A2AAdapter
    from band.adapters.a2a_gateway import A2AGatewayAdapter as A2AGatewayAdapter
    from band.adapters.codex import CodexAdapter as CodexAdapter
    from band.adapters.codex import CodexAdapterConfig as CodexAdapterConfig
    from band.adapters.acp import (
        ACPClientAdapter as ACPClientAdapter,
        ACPServer as ACPServer,
        BandACPServerAdapter as BandACPServerAdapter,
    )
    from band.adapters.gemini import GeminiAdapter as GeminiAdapter
    from band.adapters.google_adk import GoogleADKAdapter as GoogleADKAdapter
    from band.adapters.opencode import OpencodeAdapter as OpencodeAdapter
    from band.adapters.opencode import OpencodeAdapterConfig as OpencodeAdapterConfig
    from band.adapters.letta import LettaAdapter as LettaAdapter
    from band.adapters.letta import LettaAdapterConfig as LettaAdapterConfig
    from band.adapters.slack import SlackAdapter as SlackAdapter
    from band.adapters.slack import SlackApp as SlackApp
    from band.adapters.slack import SlackSessionState as SlackSessionState

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
    "SlackAdapter",
    "SlackApp",
    "SlackSessionState",
]


def __getattr__(name: str) -> type:
    """Lazy import adapters to avoid loading optional dependencies."""
    if name == "LangGraphAdapter":
        from band.adapters.langgraph import LangGraphAdapter

        return LangGraphAdapter
    elif name == "AnthropicAdapter":
        from band.adapters.anthropic import AnthropicAdapter

        return AnthropicAdapter
    elif name == "PydanticAIAdapter":
        from band.adapters.pydantic_ai import PydanticAIAdapter

        return PydanticAIAdapter
    elif name == "ClaudeSDKAdapter":
        from band.adapters.claude_sdk import ClaudeSDKAdapter

        return ClaudeSDKAdapter
    elif name == "ParlantAdapter":
        from band.adapters.parlant import ParlantAdapter

        return ParlantAdapter
    elif name == "CrewAIAdapter":
        from band.adapters.crewai import CrewAIAdapter

        return CrewAIAdapter
    elif name == "CrewAIFlowAdapter":
        from band.adapters.crewai_flow import CrewAIFlowAdapter

        return CrewAIFlowAdapter
    elif name == "A2AAdapter":
        from band.adapters.a2a import A2AAdapter

        return A2AAdapter
    elif name == "A2AGatewayAdapter":
        from band.adapters.a2a_gateway import A2AGatewayAdapter

        return A2AGatewayAdapter
    elif name == "CodexAdapter":
        from band.adapters.codex import CodexAdapter

        return CodexAdapter
    elif name == "CodexAdapterConfig":
        from band.adapters.codex import CodexAdapterConfig

        return CodexAdapterConfig
    elif name in (
        "ACPClientAdapter",
        "ACPServer",
        "BandACPServerAdapter",
    ):
        from band.adapters.acp import (
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
        from band.adapters.gemini import GeminiAdapter

        return GeminiAdapter
    elif name == "GoogleADKAdapter":
        from band.adapters.google_adk import GoogleADKAdapter

        return GoogleADKAdapter
    elif name == "OpencodeAdapter":
        from band.adapters.opencode import OpencodeAdapter

        return OpencodeAdapter
    elif name == "OpencodeAdapterConfig":
        from band.adapters.opencode import OpencodeAdapterConfig

        return OpencodeAdapterConfig
    elif name == "LettaAdapter":
        from band.adapters.letta import LettaAdapter

        return LettaAdapter
    elif name == "LettaAdapterConfig":
        from band.adapters.letta import LettaAdapterConfig

        return LettaAdapterConfig
    elif name in ("SlackAdapter", "SlackApp", "SlackSessionState"):
        from band.adapters.slack import SlackAdapter, SlackApp, SlackSessionState

        if name == "SlackAdapter":
            return SlackAdapter
        elif name == "SlackApp":
            return SlackApp
        return SlackSessionState
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
