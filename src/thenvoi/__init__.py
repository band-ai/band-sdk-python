"""
Thenvoi SDK - Connect AI agents to the Thenvoi platform.

Platform Layer:
    ThenvoiLink: WebSocket + REST transport
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
    from thenvoi import ThenvoiLink, AgentRuntime, ExecutionContext, AgentTools
    from thenvoi.platform import PlatformEvent

    async def handle_event(ctx: ExecutionContext, event: PlatformEvent):
        tools = AgentTools.from_context(ctx)
        # Your LLM logic here
        await tools.send_message("Hello!", mentions=["@john"])

    link = ThenvoiLink(agent_id="...", api_key="...", ws_url="...", rest_url="...")
    runtime = AgentRuntime(link, agent_id="...", on_execute=handle_event)
    await runtime.run()

Example (Framework-light pattern):
    from thenvoi import ThenvoiLink, RoomPresence

    link = ThenvoiLink(agent_id="...", api_key="...", ws_url="...", rest_url="...")
    presence = RoomPresence(link)
    presence.on_room_joined = my_join_handler
    presence.on_room_event = my_event_handler
    await presence.start()
    await link.run_forever()
"""

from importlib.metadata import version as _get_version, PackageNotFoundError

# Composition layer (new pattern)
from .agent import Agent

# Core types (v0.3.0)
from .core.types import AdapterFeatures, Capability, Emit
from .core.exceptions import (
    ThenvoiError,
    ThenvoiConfigError,
    ThenvoiConnectionError,
    ThenvoiToolError,
)

# Platform layer
from .platform import ThenvoiLink, PlatformEvent

# Runtime layer
from .runtime import (
    AgentRuntime,
    RoomPresence,
    Execution,
    ExecutionContext,
    ExecutionHandler,
    AgentTools,
    PlatformMessage,
    AgentConfig,
    SessionConfig,
    ConversationContext,
    render_system_prompt,
    TOOL_MODELS,
    ALL_TOOL_NAMES,
    BASE_TOOL_NAMES,
    CHAT_TOOL_NAMES,
    CONTACT_TOOL_NAMES,
    MEMORY_TOOL_NAMES,
    MCP_TOOL_PREFIX,
    mcp_tool_names,
    # Formatters
    format_message_for_llm,
    format_history_for_llm,
    build_participants_message,
    # Trackers
    ParticipantTracker,
    MessageRetryTracker,
    # Shutdown
    GracefulShutdown,
    run_with_graceful_shutdown,
)

__all__ = [
    # Composition
    "Agent",
    # Core types (v0.3.0)
    "AdapterFeatures",
    "Capability",
    "Emit",
    "ThenvoiError",
    "ThenvoiConfigError",
    "ThenvoiConnectionError",
    "ThenvoiToolError",
    # Platform
    "ThenvoiLink",
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

try:
    __version__ = _get_version("thenvoi-sdk")
except PackageNotFoundError:
    __version__ = "0.1.0"  # Fallback for editable installs
