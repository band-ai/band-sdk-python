"""Records each Band tool call's effect and activity on the turn that made it."""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from band_rest.types.agent_activity_detail import AgentActivityDetail

from band.core.turn import Turn
from band.runtime.activity import (
    MEMORY,
    REASONING,
    activity_observer_of,
    tool_call_activity,
)
from band.runtime.tools.effects import turn_effect
from band.runtime.tools.registry import TOOL_DEFINITIONS
from band.runtime.tools.types import BandTool, Surface, ToolDefinition, TurnEffect

_ToolsClass = TypeVar("_ToolsClass", bound=type)


def record_tool_result(turn: Turn, tool_name: str, result: Any) -> None:
    """Record a successful Band tool call's effect on ``turn``.

    A reply tool that returned ``None`` refused a blank send and posted nothing.
    """
    effect = turn_effect(tool_name)
    if effect is TurnEffect.REPLY and result is None:
        return
    turn.record(effect)


def _agent_tool_methods(
    cls: type,
) -> Iterator[tuple[ToolDefinition, Callable[..., Awaitable[Any]]]]:
    """Each agent tool ``cls`` itself defines, with its method."""
    for definition in TOOL_DEFINITIONS.values():
        method = cls.__dict__.get(definition.method_name)
        if definition.surface == Surface.AGENT and method is not None:
            yield definition, method


def _recording(
    tool_name: str, method: Callable[..., Awaitable[Any]]
) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(method)
    async def record_on_success(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = await method(self, *args, **kwargs)
        record_tool_result(self.turn, tool_name, result)
        return result

    return record_on_success


def records_turn_effects(cls: _ToolsClass) -> _ToolsClass:
    """Record every agent tool method's effect on the instance's ``turn``.

    Wrapping the methods themselves counts every call path: registry dispatch,
    a framework calling the method directly, and ``deliver_reply``.
    """
    for definition, method in _agent_tool_methods(cls):
        setattr(cls, definition.method_name, _recording(definition.name, method))
    return cls


MEMORY_SAVE_TOOLS = frozenset(
    {BandTool.STORE_MEMORY, BandTool.SUPERSEDE_MEMORY, BandTool.ARCHIVE_MEMORY}
)


def _tool_detail(name: str) -> AgentActivityDetail:
    return MEMORY if name in MEMORY_SAVE_TOOLS else tool_call_activity(name)


@asynccontextmanager
async def tool_activity(tools: object, name: str) -> AsyncIterator[None]:
    """Show ``name`` as the turn's activity until the tool ends.

    The observer is resolved once so the end lands on the turn the tool
    started on, even if ``tools`` is rebound to another turn meanwhile.
    """
    observer = activity_observer_of(tools)
    if observer is None:
        yield
        return
    try:
        await observer.update(_tool_detail(name))
        yield
    finally:
        await observer.update(REASONING)


@asynccontextmanager
async def model_tool_activity(tools: object, name: str) -> AsyncIterator[None]:
    """Report a model's tool call that the tools method itself does not.

    ``send_event`` is also the adapters' own telemetry path, so it reports
    only where a model invoked it.
    """
    if name != BandTool.SEND_EVENT:
        yield
        return
    async with tool_activity(tools, name):
        yield


def _observing(
    tool_name: str, method: Callable[..., Awaitable[Any]]
) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(method)
    async def observe(self: Any, *args: Any, **kwargs: Any) -> Any:
        async with tool_activity(self, tool_name):
            return await method(self, *args, **kwargs)

    return observe


def reports_tool_activity(cls: _ToolsClass) -> _ToolsClass:
    """Report every agent tool method except ``send_event`` as turn activity."""
    for definition, method in _agent_tool_methods(cls):
        if definition.name != BandTool.SEND_EVENT:
            setattr(cls, definition.method_name, _observing(definition.name, method))
    return cls
