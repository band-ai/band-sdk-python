"""Records each Band tool call's effect on the turn that made it."""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
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


def _origin(tools: TurnTools) -> object:
    """Who calls through ``tools``: its resource, or ``None`` for the turn's
    own model (tools without resource views always serve the model)."""
    return getattr(tools, "resource", None)


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
    A decline ends only its declarer's work: a subagent's decline leaves the
    model's own calls alone. SDK-driven loops stop calling the model instead
    and never consult this.
    """
    if not tools.turn.declined(_origin(tools)):
        return None
    logger.info("Refusing a model tool call: its turn already declined to reply")
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
