"""
Band SDK - Connect AI agents to the Band platform.

Platform Layer:
    BandLink: WebSocket + REST transport
    PlatformEvent: Typed events from the platform

Runtime Layer:
    AgentRuntime: Convenience wrapper (RoomPresence + Execution)
    RoomPresence: Cross-room lifecycle management
    ExecutionContext: Per-room context accumulation
    AgentTools: Platform tools bound to a room (send_message, add_participant, etc.)
    PlatformMessage: Message data structure

Configuration:
    AgentConfig: Agent-level configuration
    SessionConfig: Per-session configuration

Example (SDK-heavy pattern):
    from band import BandLink, AgentRuntime, ExecutionContext, AgentTools
    from band.platform import PlatformEvent

    async def handle_event(ctx: ExecutionContext, event: PlatformEvent):
        tools = AgentTools.from_context(ctx)
        # Your LLM logic here
        await tools.send_message("Hello!", mentions=["@john"])

    link = BandLink(agent_id="...", api_key="...", ws_url="...", rest_url="...")
    runtime = AgentRuntime(link, agent_id="...", on_execute=handle_event)
    await runtime.run()

Example (Framework-light pattern):
    from band import BandLink, RoomPresence

    link = BandLink(agent_id="...", api_key="...", ws_url="...", rest_url="...")
    presence = RoomPresence(link)
    presence.on_room_joined = my_join_handler
    presence.on_room_event = my_event_handler
    await presence.start()
    await link.run_forever()
"""

import logging
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _get_version

# Composition layer (new pattern)
from .agent import Agent
from .config.logs import LogSettings, configure_logging_from_env
from .core.exceptions import (
    BandConfigError,
    BandConnectionError,
    BandError,
    BandToolError,
    DuplicateToolError,
    LifecycleError,
    MissingDependencyError,
    RunFailed,
    StreamError,
    UnsupportedOptionError,
)
from .core.deprecation import BandDeprecationWarning, warn_deprecated
from .core.protocols import (
    AgentBackend,
    CancellationToken,
    EventSink,
    Gateway,
    ModelContext,
    ModelProvider,
    RunContext,
)
from .core.contracts import (
    BackendContext,
    DeliveryReceipt,
    ModelRequest,
    ModelResponse,
    ModelSamplingOptions,
    RunResult,
)
from .core.backends.native import NativeToolLoopBackend
from .core.run.stream import AgentStream
from .core.options import UNSET, resolve_sampling
from .core.tools import (
    FunctionTool,
    ToolContext,
    ToolSpec,
    normalize_additional_tools,
    tool,
    tool_spec_to_anthropic_schema,
    tool_spec_to_openai_schema,
)
from .core.instructions import (
    Instruction,
    InstructionMode,
    InstructionPolicy,
    normalize_instructions,
)


# Core types (v0.3.0)
from .core.types import AdapterFeatures, Capability, Emit
from .logging_config import (
    CHATTY_LOGGERS,
    STANDARD_FORMAT,
    FileStyle,
    FormatStyle,
    LoggingConfig,
    LoggingStyle,
    LogLevel,
    LogStream,
    build_logging_config,
    chatty_logger_levels,
    configure_logging,
)

# Platform layer
from .platform import BandLink, PlatformEvent

# Runtime layer
from .runtime import (
    ALL_TOOL_NAMES,
    BASE_TOOL_NAMES,
    CHAT_TOOL_NAMES,
    CONTACT_TOOL_NAMES,
    MCP_TOOL_PREFIX,
    MEMORY_TOOL_NAMES,
    TOOL_MODELS,
    AgentConfig,
    AgentRuntime,
    AgentTools,
    ConversationContext,
    Execution,
    ExecutionContext,
    ExecutionHandler,
    # Shutdown
    GracefulShutdown,
    MessageRetryTracker,
    # Trackers
    ParticipantTracker,
    PlatformMessage,
    RoomPresence,
    SessionConfig,
    build_participants_message,
    format_history_for_llm,
    # Formatters
    format_message_for_llm,
    mcp_tool_names,
    render_system_prompt,
    run_with_graceful_shutdown,
)

__all__ = [
    # Composition
    "Agent",
    # Core types (v0.3.0)
    "AdapterFeatures",
    "Capability",
    "FunctionTool",
    "ToolContext",
    "ToolSpec",
    "normalize_additional_tools",
    "tool",
    "tool_spec_to_anthropic_schema",
    "tool_spec_to_openai_schema",
    "Emit",
    "Instruction",
    "InstructionMode",
    "InstructionPolicy",
    "normalize_instructions",
    "BandError",
    "BandConfigError",
    "BandConnectionError",
    "BandToolError",
    "MissingDependencyError",
    "UnsupportedOptionError",
    "DuplicateToolError",
    "LifecycleError",
    "StreamError",
    "AgentStream",
    "RunFailed",
    "BandDeprecationWarning",
    "warn_deprecated",
    "AgentBackend",
    "ModelProvider",
    "ModelSamplingOptions",
    "NativeToolLoopBackend",
    "UNSET",
    "resolve_sampling",
    "AnthropicProvider",
    "GeminiProvider",
    "ModelContext",
    "Gateway",
    "A2AGateway",
    "ACPGateway",
    "SlackGateway",
    "EventSink",
    "CancellationToken",
    "RunContext",
    "RunResult",
    "DeliveryReceipt",
    "BackendContext",
    "ModelRequest",
    "ModelResponse",
    "FileStyle",
    "FormatStyle",
    "CHATTY_LOGGERS",
    "LogLevel",
    "LogSettings",
    "LoggingConfig",
    "LoggingStyle",
    "LogStream",
    "STANDARD_FORMAT",
    "build_logging_config",
    "chatty_logger_levels",
    "configure_logging",
    "configure_logging_from_env",
    # Platform
    "BandLink",
    "PlatformEvent",
    # Runtime - Core
    "AgentRuntime",
    "RoomPresence",
    "Execution",
    "ExecutionContext",
    "ExecutionHandler",
    "AgentTools",
    # Runtime - Types
    "PlatformMessage",
    "AgentConfig",
    "SessionConfig",
    "ConversationContext",
    # Runtime - Prompts
    "render_system_prompt",
    # Runtime - Tools
    "TOOL_MODELS",
    "ALL_TOOL_NAMES",
    "BASE_TOOL_NAMES",
    "CHAT_TOOL_NAMES",
    "CONTACT_TOOL_NAMES",
    "MEMORY_TOOL_NAMES",
    "MCP_TOOL_PREFIX",
    "mcp_tool_names",
    # Runtime - Formatters
    "format_message_for_llm",
    "format_history_for_llm",
    "build_participants_message",
    # Runtime - Trackers
    "ParticipantTracker",
    "MessageRetryTracker",
    # Runtime - Shutdown
    "GracefulShutdown",
    "run_with_graceful_shutdown",
]

_band_logger = logging.getLogger("band")
if not any(
    isinstance(handler, logging.NullHandler) for handler in _band_logger.handlers
):
    _band_logger.addHandler(logging.NullHandler())

try:
    __version__ = _get_version("band-sdk")
except PackageNotFoundError:
    __version__ = "0.1.0"  # Fallback for editable installs


def __getattr__(name: str) -> object:
    """Lazy exports for optional extras."""
    if name == "AnthropicProvider":
        from band.providers.anthropic import AnthropicProvider

        return AnthropicProvider
    if name == "GeminiProvider":
        from band.providers.gemini import GeminiProvider

        return GeminiProvider
    if name == "A2AGateway":
        from band.integrations.a2a.gateway.host import A2AGateway

        return A2AGateway
    if name == "ACPGateway":
        from band.integrations.acp.host import ACPGateway

        return ACPGateway
    if name == "SlackGateway":
        from band.integrations.slack.host import SlackGateway

        return SlackGateway
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
