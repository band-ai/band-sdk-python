"""Built-in framework adapters.

Adapters are lazily imported to avoid requiring all optional dependencies.
Install the extra you need::

    uv add band-sdk[langgraph]
    uv add band-sdk[anthropic]
    uv add band-sdk[pydantic_ai]
    uv add band-sdk[claude_sdk]
    uv add band-sdk[copilot_sdk]
    uv add band-sdk[parlant]
    uv add band-sdk[crewai]
    uv add band-sdk[gemini]
    uv add band-sdk[a2a]
    uv add band-sdk[a2a_gateway]
    uv add band-sdk[codex]
    uv add band-sdk[google_adk]
    uv add band-sdk[opencode]
    uv add band-sdk[slack]
    uv add band-sdk[strands]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from band.exports import lazy_exports

# Type-only imports for static analysis (pyrefly, mypy, etc.)
if TYPE_CHECKING:
    from band.adapters.langgraph import LangGraphAdapter as LangGraphAdapter
    from band.adapters.anthropic import AnthropicAdapter as AnthropicAdapter
    from band.adapters.pydantic_ai import PydanticAIAdapter as PydanticAIAdapter
    from band.adapters.claude_sdk import ClaudeSDKAdapter as ClaudeSDKAdapter
    from band.adapters.copilot_sdk import CopilotSDKAdapter as CopilotSDKAdapter
    from band.adapters.copilot_sdk import (
        CopilotSDKAdapterConfig as CopilotSDKAdapterConfig,
    )
    from band.adapters.copilot_acp import CopilotACPAdapter as CopilotACPAdapter
    from band.adapters.copilot_acp import (
        CopilotACPAdapterConfig as CopilotACPAdapterConfig,
    )
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
    from band.adapters.agno import AgnoAdapter as AgnoAdapter
    from band.adapters.gemini import GeminiAdapter as GeminiAdapter
    from band.adapters.google_adk import GoogleADKAdapter as GoogleADKAdapter
    from band.adapters.opencode import OpencodeAdapter as OpencodeAdapter
    from band.adapters.opencode import OpencodeAdapterConfig as OpencodeAdapterConfig
    from band.adapters.letta import LettaAdapter as LettaAdapter
    from band.adapters.letta import LettaAdapterConfig as LettaAdapterConfig
    from band.adapters.slack import SlackAdapter as SlackAdapter
    from band.adapters.slack import SlackApp as SlackApp
    from band.adapters.slack import SlackSessionState as SlackSessionState
    from band.adapters.strands import StrandsAdapter as StrandsAdapter

__all__, __getattr__, __dir__ = lazy_exports(
    __name__,
    {
        "langgraph": ("LangGraphAdapter",),
        "anthropic": ("AnthropicAdapter",),
        "pydantic_ai": ("PydanticAIAdapter",),
        "claude_sdk": ("ClaudeSDKAdapter",),
        "copilot_sdk": ("CopilotSDKAdapter", "CopilotSDKAdapterConfig"),
        "copilot_acp": ("CopilotACPAdapter", "CopilotACPAdapterConfig"),
        "parlant": ("ParlantAdapter",),
        "crewai": ("CrewAIAdapter",),
        "crewai_flow": ("CrewAIFlowAdapter",),
        "a2a": ("A2AAdapter",),
        "a2a_gateway": ("A2AGatewayAdapter",),
        "codex": ("CodexAdapter", "CodexAdapterConfig"),
        "acp": ("ACPClientAdapter", "ACPServer", "BandACPServerAdapter"),
        "agno": ("AgnoAdapter",),
        "gemini": ("GeminiAdapter",),
        "google_adk": ("GoogleADKAdapter",),
        "opencode": ("OpencodeAdapter", "OpencodeAdapterConfig"),
        "letta": ("LettaAdapter", "LettaAdapterConfig"),
        "slack": ("SlackAdapter", "SlackApp", "SlackSessionState"),
        "strands": ("StrandsAdapter",),
    },
)
