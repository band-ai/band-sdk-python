"""Records each Band tool call's effect on the turn that made it."""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from band.core.turn import Turn
from band.runtime.tools.effects import turn_effect
from band.runtime.tools.registry import TOOL_DEFINITIONS
from band.runtime.tools.types import Surface, TurnEffect

_ToolsClass = TypeVar("_ToolsClass", bound=type)


def record_tool_result(turn: Turn, tool_name: str, result: Any) -> None:
    """Record a successful Band tool call's effect on ``turn``.

    A reply tool that returned ``None`` refused a blank send and posted nothing.
    """
    effect = turn_effect(tool_name)
    if effect is TurnEffect.REPLY and result is None:
        return
    turn.record(effect)


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
    for definition in TOOL_DEFINITIONS.values():
        method = cls.__dict__.get(definition.method_name)
        if definition.surface == Surface.AGENT and method is not None:
            setattr(cls, definition.method_name, _recording(definition.name, method))
    return cls
