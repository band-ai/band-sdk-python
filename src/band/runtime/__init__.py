"""
Band Runtime Layer - Agent execution and room management.

Components:
    RoomPresence: Cross-room lifecycle management
    Execution: Per-room execution protocol
    ExecutionContext: Default execution implementation (context accumulation)
    AgentRuntime: Convenience wrapper combining presence + execution
    AgentTools: Tool interface for LLM platform interaction

Utilities:
    formatters: Pure functions for message formatting
    prompts: System prompt rendering
    MessageRetryTracker: Message retry tracking

Shutdown:
    GracefulShutdown: Signal handler for graceful agent termination
    run_with_graceful_shutdown: Convenience function to run agent with signal handling
"""

# Types
from .execution import Execution, ExecutionContext, ExecutionHandler

# Utilities
from .formatters import (
    build_participants_message,
    format_history_for_llm,
    format_message_for_llm,
)
from .oneshot import OneShotEnvelopeError, OneShotInvoker, OneShotStatus

# Core runtime components
from .presence import RoomPresence
from .prompts import BASE_INSTRUCTIONS, TEMPLATES, render_system_prompt
from .retry_tracker import MessageRetryTracker
from .runtime import AgentRuntime
from .shutdown import GracefulShutdown, run_with_graceful_shutdown

# Tools
from .tools import (
    ALL_TOOL_NAMES,
    BASE_TOOL_NAMES,
    CHAT_TOOL_NAMES,
    CONTACT_TOOL_NAMES,
    MCP_TOOL_PREFIX,
    MEMORY_TOOL_NAMES,
    READ_ONLY_TOOL_NAMES,
    TOOL_MODELS,
    AgentTools,
    HumanTools,
    mcp_tool_names,
)
from .types import (
    AgentConfig,
    ConversationContext,
    MessageHandler,
    PlatformMessage,
    SessionConfig,
)

__all__ = [
    "ALL_TOOL_NAMES",
    "BASE_INSTRUCTIONS",
    "BASE_TOOL_NAMES",
    "CHAT_TOOL_NAMES",
    "CONTACT_TOOL_NAMES",
    "MCP_TOOL_PREFIX",
    "MEMORY_TOOL_NAMES",
    "READ_ONLY_TOOL_NAMES",
    "TEMPLATES",
    "TOOL_MODELS",
    # Types
    "AgentConfig",
    "AgentRuntime",
    # Tools
    "AgentTools",
    "ConversationContext",
    "Execution",
    "ExecutionContext",
    "ExecutionHandler",
    # Shutdown
    "GracefulShutdown",
    "HumanTools",
    "MessageHandler",
    # Trackers
    "MessageRetryTracker",
    "OneShotEnvelopeError",
    "OneShotInvoker",
    "OneShotStatus",
    "PlatformMessage",
    # Core components
    "RoomPresence",
    "SessionConfig",
    "build_participants_message",
    "format_history_for_llm",
    # Formatters
    "format_message_for_llm",
    "mcp_tool_names",
    # Prompts
    "render_system_prompt",
    "run_with_graceful_shutdown",
]
