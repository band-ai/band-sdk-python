"""Which Band room's tools a Parlant tool call acts on.

Parlant runs tools in its own tasks, so a ``ContextVar`` set by the adapter
never reaches them. The adapter instead binds the room's ``AgentTools`` to the
Parlant session id for the length of a turn, and every tool resolves it from
``context.session_id``.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# Parlant passes each tool its ToolContext under this parameter name.
CONTEXT_PARAMETER = "context"

# What a tool answers the model with when its Parlant session has no Band room
# bound — a session that outlived its room, or a tool called before one was set.
NO_SESSION_TOOLS_ERROR = "Error: No tools available in current context"

_session_tools: dict[str, Any] = {}


class NoSessionTools(Exception):
    """A tool ran while its Parlant session had no Band room bound to it."""


def set_session_tools(session_id: str, tools: Any | None) -> None:
    """Bind (or, with ``None``, unbind) a Parlant session to a room's tools."""
    if tools is None:
        _session_tools.pop(session_id, None)
    else:
        _session_tools[session_id] = tools
    logger.debug("Set tools for session %s: %s", session_id, tools is not None)


def get_session_tools(session_id: str) -> Any | None:
    """The room tools bound to a Parlant session, if any."""
    tools = _session_tools.get(session_id)
    logger.debug(
        "Get tools for session_id=%s: found=%s, available_sessions=%s",
        session_id,
        tools is not None,
        list(_session_tools.keys()),
    )
    return tools


def require_session_tools(context: Any) -> Any:
    """The room's ``AgentTools`` for this Parlant session, or refuse to run.

    Raising rather than returning ``None`` is what lets the shared tool guard
    turn the refusal into the model-visible error from one place.
    """
    tools = get_session_tools(context.session_id)
    if not tools:
        raise NoSessionTools(context.session_id)
    return tools


@contextmanager
def bound_session_tools(*, session_id: str, tools: Any) -> Iterator[None]:
    """Bind a session to a room's tools for one turn, unbinding on exit."""
    set_session_tools(session_id, tools)
    try:
        yield
    finally:
        set_session_tools(session_id, None)


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
    return None  # Always returns None, tools now accessed via session_id
