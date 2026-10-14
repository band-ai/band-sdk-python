"""Adapters at each level of batch-turn support, and a batch of originals."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from band.core.exceptions import InputNotAccepted
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput, PlatformMessage
from tests.framework_conformance.invocationkit import turn_input
from tests.runtime.conftest import platform_msg

Entry = Callable[[SimpleAdapter[Any], AgentInput], Awaitable[None]]


def batch_input() -> AgentInput:
    """A turn over two originals from different senders."""
    return turn_input(
        platform_msg("m1", sender_id="alice"), platform_msg("m2", sender_id="bob")
    )


class UndeclaredAdapter(SimpleAdapter[Any]):
    """Never declared batch support."""

    async def on_message(self, *args: Any, **kwargs: Any) -> None: ...


class UnbuiltAdapter(UndeclaredAdapter):
    """Declares batch support but has no ``on_turn`` turn builder."""

    supports_batch_input = True


class TurnBuilderAdapter(SimpleAdapter[Any]):
    """One turn builder behind both entry points; declines when told to."""

    supports_batch_input = True

    def __init__(self, *, declines: bool) -> None:
        super().__init__()
        self.declines = declines
        self.turns: list[tuple[str, ...]] = []

    async def on_message(self, msg: PlatformMessage, *args: Any, **kwargs: Any) -> None:
        await self.on_turn((msg,), *args, **kwargs)

    async def on_turn(
        self,
        messages: Sequence[PlatformMessage],
        tools: AgentToolsProtocol,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self.turns.append(tuple(m.id for m in messages))
        if self.declines:
            await tools.no_reply("nothing owed")


class RefusingAdapter(TurnBuilderAdapter):
    """Its provider refuses every turn's input."""

    async def on_turn(self, *args: Any, **kwargs: Any) -> None:
        raise InputNotAccepted("provider refused the input")
