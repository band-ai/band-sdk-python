"""What a tool call shows as its turn's activity, and the one place it is reported."""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Hashable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from inspect import getattr_static
from typing import Any

from band_rest.types.agent_activity_detail import AgentActivityDetail

from band.runtime.activity import (
    MEMORY,
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

WRAPPED_TOOL_ACTIVITY = "band_wrapped_tool_activity"

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


async def _start(
    observer: ActivityObserver, key: Hashable, detail: AgentActivityDetail
) -> None:
    """Activity is best-effort: a failed report never changes the tool's outcome."""
    try:
        await observer.start(key, detail)
    except Exception:
        logger.warning("Could not report %s activity", detail.kind, exc_info=True)


async def _end(observer: ActivityObserver, key: Hashable) -> None:
    try:
        await observer.end(key)
    except Exception:
        logger.warning("Could not report the end of an activity", exc_info=True)


@contextmanager
def suppress_tool_activity() -> Iterator[None]:
    """Leave activity reporting to the enclosing owner or native events."""
    token = _showing_tool.set(True)
    try:
        yield
    finally:
        _showing_tool.reset(token)


def require_activity_authority(observer: ActivityObserver | None) -> None:
    """Recording-only observers need no owner; scoped observers must remain valid."""
    if (
        observer is not None
        and getattr_static(observer, "authoritative", None) is not None
        and not getattr(observer, "authoritative", True)
    ):
        raise asyncio.CancelledError


@asynccontextmanager
async def execution_activity(
    tools: object, detail: AgentActivityDetail
) -> AsyncIterator[None]:
    """Capture one observer and suppress duplicate nested execution wrappers."""
    observer = activity_observer_of(tools)
    require_activity_authority(observer)
    if observer is None or _showing_tool.get():
        yield
        return
    key = object()
    try:
        with suppress_tool_activity():
            await _start(observer, key, detail)
            require_activity_authority(observer)
            yield
    finally:
        await _end(observer, key)


@asynccontextmanager
async def tool_activity(tools: object, name: str) -> AsyncIterator[None]:
    """Report a registered Band tool, including genuine memory writes."""
    async with execution_activity(tools, tool_detail(name)):
        yield


@asynccontextmanager
async def native_tool_activity(tools: object, name: str) -> AsyncIterator[None]:
    """Report actual native/custom execution independently of name spelling."""
    async with execution_activity(tools, tool_call_activity(name)):
        yield


async def start_native_tool_activity(
    tools: object, key: Hashable, name: str | None
) -> None:
    if observer := activity_observer_of(tools):
        await _start(observer, key, tool_call_activity(name))


async def rename_native_tool_activity(
    tools: object, key: Hashable, name: str | None
) -> None:
    if observer := activity_observer_of(tools):
        try:
            rename = getattr(observer, "rename", None)
            if getattr_static(observer, "rename", None) is not None and callable(
                rename
            ):
                await rename(key, tool_call_activity(name))
        except Exception:
            logger.warning(
                "Could not report an activity identity update", exc_info=True
            )


async def start_tool_activity(tools: object, key: Hashable, name: str) -> None:
    """Show ``name`` as running under ``key``, for a tool whose start and end
    arrive as separate events; starting a running key again renames it."""
    if observer := activity_observer_of(tools):
        await _start(observer, key, tool_detail(name))


async def start_memory_activity(tools: object, key: Hashable) -> None:
    if observer := activity_observer_of(tools):
        await _start(observer, key, MEMORY)


async def end_activity(tools: object, key: Hashable) -> None:
    """End what ``key`` shows; an unknown or already ended key changes nothing."""
    if observer := activity_observer_of(tools):
        await _end(observer, key)


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
