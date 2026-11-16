"""Records each Band tool call's effect on the turn that made it."""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable, Hashable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Protocol

from band.runtime.tools.effects import DECLINED_TURN_REFUSAL, turn_effect
from band.runtime.tools.registry import ToolsClass, agent_tool_methods
from band.runtime.tools.types import TurnEffect

if TYPE_CHECKING:
    from band.core.turn import Turn

logger = logging.getLogger(__name__)


class TurnTools(Protocol):
    """Tools that carry the turn their calls record on."""

    turn: Turn


_calling_as: ContextVar[Hashable | None] = ContextVar("calling_as", default=None)


@contextmanager
def calling_as(origin: Hashable | None) -> Iterator[None]:
    """Run a provider call as ``origin``, a subagent the provider names.

    The turn's tools are shared, so the caller rides on the call's context
    rather than on a copy of them; ``None`` is the turn's own model.
    """
    token = _calling_as.set(origin)
    try:
        yield
    finally:
        _calling_as.reset(token)


def _origin(tools: TurnTools) -> Hashable | None:
    """Who is calling; ``None`` for the turn's own model.

    A provider-named caller comes first, then the resource a tools view serves.
    """
    origin = _calling_as.get()
    return origin if origin is not None else getattr(tools, "origin", None)


def record_effect(tools: TurnTools, effect: TurnEffect) -> None:
    """Record ``effect`` on the turn, made by whoever calls through ``tools``."""
    tools.turn.record(effect, origin=_origin(tools))


def record_tool_result(tools: TurnTools, tool_name: str, result: Any) -> None:
    """Record a successful Band tool call's effect on ``tools``' turn.

    A reply tool that returned ``None`` refused a blank send and posted nothing.
    """
    effect = turn_effect(tool_name)
    if effect is TurnEffect.REPLY and result is None:
        return
    record_effect(tools, effect)


def declined_turn_refusal(tools: TurnTools) -> str | None:
    """The refusal for a tool call through ``tools`` after its caller declined.

    A provider-run loop keeps calling tools after the model declines; the SDK
    cannot stop that loop, so it refuses the calls at the point they enter.
    The model's decline refuses every later call in the turn; a subagent's
    refuses only its own. SDK-driven loops stop calling the model instead and
    never consult this.
    """
    origin = _origin(tools)
    if not tools.turn.declined(origin):
        return None
    caller = "the model" if origin is None else "a subagent"
    logger.info("Refusing a tool call from %s: its turn ended in a decline", caller)
    return DECLINED_TURN_REFUSAL


def _recording(
    tool_name: str, method: Callable[..., Awaitable[Any]]
) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(method)
    async def record_on_success(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = await method(self, *args, **kwargs)
        record_tool_result(self, tool_name, result)
        return result

    return record_on_success


def records_turn_effects(cls: ToolsClass) -> ToolsClass:
    """Record every agent tool method's effect on the instance's ``turn``.

    Wrapping the methods themselves counts every call path: registry dispatch,
    a framework calling the method directly, and ``deliver_reply``.
    """
    for definition, method in agent_tool_methods(cls):
        setattr(cls, definition.method_name, _recording(definition.name, method))
    return cls
