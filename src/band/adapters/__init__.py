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
    from band.adapters.a2a import A2AAdapter as A2AAdapter
    from band.adapters.a2a import A2AAdapterConfig as A2AAdapterConfig
    from band.adapters.a2a_gateway import (
        A2AGatewayAdapter as A2AGatewayAdapter,
    )
    from band.adapters.a2a_gateway import (
        A2AGatewayAdapterConfig as A2AGatewayAdapterConfig,
    )
    from band.adapters.acp import (
        ACPClientAdapter as ACPClientAdapter,
    )
    from band.adapters.acp import (
        ACPClientAdapterConfig as ACPClientAdapterConfig,
    )
    from band.adapters.acp import (
        ACPConfigRequest as ACPConfigRequest,
    )
    from band.adapters.acp import (
        ACPServer as ACPServer,
    )
    from band.adapters.acp import (
        BandACPServerAdapter as BandACPServerAdapter,
    )
    from band.adapters.acp import (
        BandACPServerAdapterConfig as BandACPServerAdapterConfig,
    )
    from band.adapters.agno import AgnoAdapter as AgnoAdapter
    from band.adapters.agno import AgnoAdapterConfig as AgnoAdapterConfig
    from band.adapters.anthropic import AnthropicAdapter as AnthropicAdapter
    from band.adapters.anthropic import (
        AnthropicAdapterConfig as AnthropicAdapterConfig,
    )
    from band.adapters.claude_sdk import (
        ClaudeApprovalOptions as ClaudeApprovalOptions,
    )
    from band.adapters.claude_sdk import ClaudeCLIOptions as ClaudeCLIOptions
    from band.adapters.claude_sdk import ClaudeSDKAdapter as ClaudeSDKAdapter
    from band.adapters.claude_sdk import (
        ClaudeSDKAdapterConfig as ClaudeSDKAdapterConfig,
    )
    from band.adapters.codex import (
        CodexAdapter as CodexAdapter,
    )
    from band.adapters.codex import (
        CodexAdapterConfig as CodexAdapterConfig,
    )
    from band.adapters.copilot_acp import (
        CopilotACPAdapter as CopilotACPAdapter,
    )
    from band.adapters.copilot_acp import (
        CopilotACPAdapterConfig as CopilotACPAdapterConfig,
    )
    from band.adapters.copilot_sdk import (
        CopilotSDKAdapter as CopilotSDKAdapter,
    )
    from band.adapters.copilot_sdk import (
        CopilotSDKAdapterConfig as CopilotSDKAdapterConfig,
    )
    from band.adapters.crewai import CrewAIAdapter as CrewAIAdapter
    from band.adapters.crewai import CrewAIAdapterConfig as CrewAIAdapterConfig
    from band.adapters.crewai_flow import CrewAIFlowAdapter as CrewAIFlowAdapter
    from band.adapters.crewai_flow import (
        CrewAIFlowAdapterConfig as CrewAIFlowAdapterConfig,
    )
    from band.adapters.cursor_acp import (
        CursorACPAdapter as CursorACPAdapter,
    )
    from band.adapters.cursor_acp import (
        CursorACPAdapterConfig as CursorACPAdapterConfig,
    )
    from band.adapters.gemini import GeminiAdapter as GeminiAdapter
    from band.adapters.gemini import (
        GeminiAdapterConfig as GeminiAdapterConfig,
    )
    from band.adapters.google_adk import GoogleADKAdapter as GoogleADKAdapter
    from band.adapters.google_adk import (
        GoogleADKAdapterConfig as GoogleADKAdapterConfig,
    )
    from band.adapters.langgraph import LangGraphAdapter as LangGraphAdapter
    from band.adapters.langgraph import (
        LangGraphAdapterConfig as LangGraphAdapterConfig,
    )
    from band.adapters.letta import (
        LettaAdapter as LettaAdapter,
    )
    from band.adapters.letta import (
        LettaAdapterConfig as LettaAdapterConfig,
    )
    from band.adapters.omp_acp import (
        OmpACPAdapter as OmpACPAdapter,
    )
    from band.adapters.omp_acp import (
        OmpACPAdapterConfig as OmpACPAdapterConfig,
    )
    from band.adapters.opencode import (
        OpencodeAdapter as OpencodeAdapter,
    )
    from band.adapters.opencode import (
        OpencodeAdapterConfig as OpencodeAdapterConfig,
    )
    from band.adapters.parlant import ParlantAdapter as ParlantAdapter
    from band.adapters.parlant import (
        ParlantAdapterConfig as ParlantAdapterConfig,
    )
    from band.adapters.pydantic_ai import PydanticAIAdapter as PydanticAIAdapter
    from band.adapters.pydantic_ai import (
        PydanticAIAdapterConfig as PydanticAIAdapterConfig,
    )
    from band.adapters.slack import (
        SlackAdapter as SlackAdapter,
    )
    from band.adapters.slack import (
        SlackAdapterConfig as SlackAdapterConfig,
    )
    from band.adapters.slack import (
        SlackApp as SlackApp,
    )
    from band.adapters.slack import (
        SlackSessionState as SlackSessionState,
    )
    from band.adapters.strands import StrandsAdapter as StrandsAdapter
    from band.adapters.strands import (
        StrandsAdapterConfig as StrandsAdapterConfig,
    )

__all__, __getattr__ = lazy_exports(
    __name__,
    langgraph=["LangGraphAdapter", "LangGraphAdapterConfig"],
    anthropic=["AnthropicAdapter", "AnthropicAdapterConfig"],
    pydantic_ai=["PydanticAIAdapter", "PydanticAIAdapterConfig"],
    claude_sdk=[
        "ClaudeApprovalOptions",
        "ClaudeCLIOptions",
        "ClaudeSDKAdapter",
        "ClaudeSDKAdapterConfig",
    ],
    copilot_sdk=["CopilotSDKAdapter", "CopilotSDKAdapterConfig"],
    copilot_acp=["CopilotACPAdapter", "CopilotACPAdapterConfig"],
    cursor_acp=["CursorACPAdapter", "CursorACPAdapterConfig"],
    omp_acp=["OmpACPAdapter", "OmpACPAdapterConfig"],
    parlant=["ParlantAdapter", "ParlantAdapterConfig"],
    crewai=["CrewAIAdapter", "CrewAIAdapterConfig"],
    crewai_flow=["CrewAIFlowAdapter", "CrewAIFlowAdapterConfig"],
    a2a=["A2AAdapter", "A2AAdapterConfig"],
    a2a_gateway=["A2AGatewayAdapter", "A2AGatewayAdapterConfig"],
    codex=["CodexAdapter", "CodexAdapterConfig"],
    acp=[
        "ACPConfigRequest",
        "ACPClientAdapter",
        "ACPClientAdapterConfig",
        "ACPServer",
        "BandACPServerAdapter",
        "BandACPServerAdapterConfig",
    ],
    agno=["AgnoAdapter", "AgnoAdapterConfig"],
    gemini=["GeminiAdapter", "GeminiAdapterConfig"],
    google_adk=["GoogleADKAdapter", "GoogleADKAdapterConfig"],
    opencode=["OpencodeAdapter", "OpencodeAdapterConfig"],
    letta=["LettaAdapter", "LettaAdapterConfig"],
    slack=["SlackAdapter", "SlackAdapterConfig", "SlackApp", "SlackSessionState"],
    strands=["StrandsAdapter", "StrandsAdapterConfig"],
)
