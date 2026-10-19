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

from __future__ import annotations

from band.runtime.agentconfig import AgentConfig
from band.runtime.callbacks import MessageHandler
from band.runtime.context import ConversationContext

# Types
from band.runtime.execution import Execution, ExecutionContext, ExecutionHandler

# Utilities
from band.runtime.formatters import (
    build_participants_message,
    format_history_for_llm,
    format_message_for_llm,
)
from band.runtime.messages import PlatformMessage
from band.runtime.oneshot import OneShotEnvelopeError, OneShotInvoker, OneShotStatus

# Core runtime components
from band.runtime.presence import RoomPresence
from band.runtime.prompts import BASE_INSTRUCTIONS, TEMPLATES, render_system_prompt
from band.runtime.retry_tracker import MessageRetryTracker
from band.runtime.runtime import AgentRuntime
from band.runtime.sessionconfig import SessionConfig
from band.runtime.shutdown import GracefulShutdown, run_with_graceful_shutdown

# Tools
from band.runtime.tools import (
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
