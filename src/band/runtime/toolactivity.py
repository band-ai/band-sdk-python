"""What a tool call shows as its turn's activity, and the one place it is reported."""

from __future__ import annotations

import functools
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any

from band_rest.types.agent_activity_detail import AgentActivityDetail

from band.runtime.activity import (
    MEMORY,
    REASONING,
    ActivityObserver,
    activity_observer_of,
    tool_call_activity,
)
from band.runtime.tools.effects import turn_effect
from band.runtime.tools.registry import (
    MEMORY_TOOL_NAMES,
    ToolsClass,
    agent_tool_methods,
)
from band.runtime.tools.types import BandTool

logger = logging.getLogger(__name__)

MEMORY_SAVE_TOOLS = frozenset(
    name for name in MEMORY_TOOL_NAMES if turn_effect(name).did_work
)

# Set while a tool call is shown, so a Band tool another tool calls stays hidden.
_showing_tool: ContextVar[bool] = ContextVar("band_showing_tool", default=False)


def tool_detail(name: str) -> AgentActivityDetail:
    """What the room sees while ``name`` runs."""
    return MEMORY if name in MEMORY_SAVE_TOOLS else tool_call_activity(name)


def method_reports_activity(name: str) -> bool:
    """Whether a tools method reports itself; adapters post their own events
    through ``send_event``, so only a model's call to it reports."""
    return name != BandTool.SEND_EVENT


async def _show(observer: ActivityObserver, detail: AgentActivityDetail) -> None:
    """Activity is best-effort: a failed report never changes the tool's outcome."""
    try:
        await observer.update(detail)
    except Exception:
        logger.warning("Could not report %s activity", detail.kind, exc_info=True)


@contextmanager
def suppress_tool_activity() -> Iterator[None]:
    """Leave activity reporting to the enclosing owner or native events."""
    token = _showing_tool.set(True)
    try:
        yield
    finally:
        _showing_tool.reset(token)


@asynccontextmanager
async def tool_activity(tools: object, name: str) -> AsyncIterator[None]:
    """Show ``name`` as the turn's activity until the tool ends.

    Only the outermost call reports, so a tool that calls other Band tools
    shows as itself throughout. The observer is resolved once so the end lands
    on the turn the tool started on, even if ``tools`` is rebound meanwhile.
    """
    observer = activity_observer_of(tools)
    if observer is None or _showing_tool.get():
        yield
        return
    try:
        with suppress_tool_activity():
            await _show(observer, tool_detail(name))
            yield
    finally:
        await _show(observer, REASONING)


async def _show_on(tools: object, detail: AgentActivityDetail) -> None:
    if observer := activity_observer_of(tools):
        await _show(observer, detail)


async def report_tool_activity(tools: object, name: str) -> None:
    """Show ``name`` for a tool whose start and end arrive as separate events."""
    await _show_on(tools, tool_detail(name))


async def end_tool_activity(tools: object) -> None:
    await _show_on(tools, REASONING)


async def report_memory_activity(tools: object) -> None:
    await _show_on(tools, MEMORY)


def _observing(
    tool_name: str, method: Callable[..., Awaitable[Any]]
) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(method)
    async def observe(self: Any, *args: Any, **kwargs: Any) -> Any:
        async with tool_activity(self, tool_name):
            return await method(self, *args, **kwargs)

    return observe


def reports_tool_activity(cls: ToolsClass) -> ToolsClass:
    """Report each agent tool method that ``method_reports_activity`` selects."""
    for definition, method in agent_tool_methods(cls):
        if method_reports_activity(definition.name):
            setattr(cls, definition.method_name, _observing(definition.name, method))
    return cls
