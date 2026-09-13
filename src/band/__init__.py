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
)

# Core types (v0.3.0)
from .core.types import AdapterFeatures, Capability, Emit, FeatureKwargs
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
from .workspaces import create_room_workspace_resolver

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
    "ALL_TOOL_NAMES",
    "BASE_TOOL_NAMES",
    "CHATTY_LOGGERS",
    "CHAT_TOOL_NAMES",
    "CONTACT_TOOL_NAMES",
    "MCP_TOOL_PREFIX",
    "MEMORY_TOOL_NAMES",
    "STANDARD_FORMAT",
    # Runtime - Tools
    "TOOL_MODELS",
    # Core types (v0.3.0)
    "AdapterFeatures",
    # Composition
    "Agent",
    "AgentConfig",
    # Runtime - Core
    "AgentRuntime",
    "AgentTools",
    "BandConfigError",
    "BandConnectionError",
    "BandError",
    # Platform
    "BandLink",
    "BandToolError",
    "Capability",
    "ConversationContext",
    "Emit",
    "Execution",
    "ExecutionContext",
    "ExecutionHandler",
    "FeatureKwargs",
    "FileStyle",
    "FormatStyle",
    # Runtime - Shutdown
    "GracefulShutdown",
    "LogLevel",
    "LogSettings",
    "LogStream",
    "LoggingConfig",
    "LoggingStyle",
    # Runtime - Trackers
    "MessageRetryTracker",
    "PlatformEvent",
    # Runtime - Types
    "PlatformMessage",
    "RoomPresence",
    "SessionConfig",
    "build_logging_config",
    "build_participants_message",
    "chatty_logger_levels",
    "configure_logging",
    "configure_logging_from_env",
    "create_room_workspace_resolver",
    "format_history_for_llm",
    # Runtime - Formatters
    "format_message_for_llm",
    "mcp_tool_names",
    # Runtime - Prompts
    "render_system_prompt",
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
