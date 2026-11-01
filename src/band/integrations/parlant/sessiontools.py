"""Which Band room's tools a Parlant tool call acts on.

Parlant runs tools in its own tasks, so a ``ContextVar`` set by the adapter
never reaches them. The adapter instead binds the room's ``AgentTools`` to the
Parlant session id for the length of a turn, and every tool resolves it from
``context.session_id``.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from band.core.exceptions import CleanupIncompleteError
from band.core.protocols import AgentToolsProtocol
from band.core.turn_lifecycle import (
    resource_tools,
    TaskRole,
    TurnTask,
    bind_turn_task,
    terminal_deadline,
)
from band.runtime.activity import activity_observer_of
from band.runtime.toolactivity import require_activity_authority

logger = logging.getLogger(__name__)

# Parlant passes each tool its ToolContext under this parameter name.
CONTEXT_PARAMETER = "context"

# What a tool answers the model with when its Parlant session has no Band room
# bound — a session that outlived its room, or a tool called before one was set.
NO_SESSION_TOOLS_ERROR = "Error: No tools available in current context"

_session_tools: dict[str, EngineOrigin] = {}
ENGINE_ORIGIN = "_band_engine_origin"
_plugin_tools: ContextVar[tuple[str, AgentToolsProtocol] | None] = ContextVar(
    "parlant_plugin_tools", default=None
)


@dataclass(eq=False)
class EngineOrigin:
    tools: AgentToolsProtocol
    task: asyncio.Task[Any] | None = None
    bodies: set[TurnTask] = field(default_factory=set)

    def revoke(self) -> None:
        deadline = terminal_deadline()
        for owner in tuple(self.bodies):
            owner.terminate(deadline=deadline)

    async def drain(self) -> None:
        for owner in tuple(self.bodies):
            if owner.deadline is not None:
                if not await owner.drain():
                    raise CleanupIncompleteError(
                        "Parlant plugin body cleanup is incomplete"
                    )
            else:
                await asyncio.wait({owner.task})


async def capture_engine_origin(
    context: Any, payload: Any, exception: Exception | None = None
) -> Any:
    from parlant.core.engines.alpha.hooks import EngineHookResult  # noqa: PLC0415 -- optional provider dependency

    origin = _session_tools.get(str(context.session.id))
    task = asyncio.current_task()
    if origin is not None and task is not None and origin.task in (None, task):
        origin.task = task
        setattr(context, ENGINE_ORIGIN, origin)
    return EngineHookResult.CALL_NEXT


class NoSessionTools(Exception):
    """A tool ran while its Parlant session had no Band room bound to it."""


def set_session_tools(session_id: str, tools: Any | None) -> None:
    """Bind (or, with ``None``, unbind) a Parlant session to a room's tools."""
    if tools is None:
        _session_tools.pop(session_id, None)
    else:
        _session_tools[session_id] = EngineOrigin(tools)
    logger.debug("Set tools for session %s: %s", session_id, tools is not None)


def get_session_tools(session_id: str) -> Any | None:
    """The room tools bound to a Parlant session, if any."""
    origin = _session_tools.get(session_id)
    tools = origin.tools if origin is not None else None
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
    if (bound := _plugin_tools.get()) is not None and bound[0] == str(
        context.session_id
    ):
        return bound[1]
    from parlant.core.engines.alpha.entity_context import EntityContext  # noqa: PLC0415 -- optional provider dependency

    engine = EntityContext.get()
    origin = getattr(engine, ENGINE_ORIGIN, None)
    if (
        not isinstance(origin, EngineOrigin)
        or origin.task is None
        or origin.task.done()
        or origin.task.cancelling()
        or str(engine.session.id) != str(context.session_id)
    ):
        raise NoSessionTools(context.session_id)
    require_activity_authority(activity_observer_of(origin.tools))
    return origin.tools


@contextmanager
def bound_plugin_tools(context: Any) -> Iterator[AgentToolsProtocol]:
    tools = require_session_tools(context)
    task = asyncio.current_task()
    if task is None:
        raise NoSessionTools(context.session_id)
    owner = bind_turn_task(tools, task, role=TaskRole.RESOURCE)
    from parlant.core.engines.alpha.entity_context import EntityContext  # noqa: PLC0415 -- optional provider dependency

    origin = getattr(EntityContext.get(), ENGINE_ORIGIN, None)
    if not isinstance(origin, EngineOrigin):
        raise NoSessionTools(context.session_id)
    origin.bodies.add(owner)
    task.add_done_callback(lambda _: origin.bodies.discard(owner))
    captured = resource_tools(tools, owner)
    token = _plugin_tools.set((str(context.session_id), captured))
    try:
        yield captured
    finally:
        _plugin_tools.reset(token)


@contextmanager
def bound_session_tools(*, session_id: str, tools: Any) -> Iterator[EngineOrigin]:
    """Bind a session to a room's tools for one turn, unbinding on exit."""
    set_session_tools(session_id, tools)
    origin = _session_tools[session_id]
    try:
        yield origin
    finally:
        if _session_tools.get(session_id) is origin:
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
    return None  # Tools are bound per Parlant session, never globally.
