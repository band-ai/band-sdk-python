"""Session-keyed registry for per-session Parlant tools and message tracking."""

from __future__ import annotations

import logging
import warnings
from typing import Any

logger = logging.getLogger(__name__)

# Session-keyed registry to hold tools for each session.
# This approach works across async contexts (unlike ContextVar).
_session_tools: dict[str, Any] = {}

# Track whether send_message was called for each session.
# This helps the adapter know if it needs to forward Parlant's response.
_session_message_sent: dict[str, bool] = {}


def set_session_tools(session_id: str, tools: Any | None) -> None:
    """Set the tools for a specific Parlant session."""
    if tools is None:
        _session_tools.pop(session_id, None)
        _session_message_sent.pop(session_id, None)
    else:
        _session_tools[session_id] = tools
        _session_message_sent[session_id] = False
    logger.debug("Set tools for session %s: %s", session_id, tools is not None)


def get_session_tools(session_id: str) -> Any | None:
    """Get the tools for a specific Parlant session."""
    tools = _session_tools.get(session_id)
    logger.debug(
        "Get tools for session_id=%s: found=%s, available_sessions=%s",
        session_id,
        tools is not None,
        list(_session_tools.keys()),
    )
    return tools


def mark_message_sent(session_id: str) -> None:
    """Mark that a message was sent via the send_message tool for this session."""
    _session_message_sent[session_id] = True
    logger.debug("Marked message sent for session %s", session_id)


def was_message_sent(session_id: str) -> bool:
    """Check if a message was sent via the send_message tool for this session."""
    return _session_message_sent.get(session_id, False)


# Keep old API for backwards compatibility (deprecated).
def set_current_tools(tools: Any | None) -> None:
    """Deprecated: Use set_session_tools instead."""
    warnings.warn(
        "set_current_tools is deprecated, use set_session_tools instead",
        DeprecationWarning,
        stacklevel=2,
    )


def get_current_tools() -> Any | None:
    """Deprecated: Use get_session_tools instead."""
    warnings.warn(
        "get_current_tools is deprecated, use get_session_tools instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return None
