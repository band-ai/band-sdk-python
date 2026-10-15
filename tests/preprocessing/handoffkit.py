"""Real preprocessing and adapter boundaries with controllable setup failures."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from band.core.handoff import context_handoff
from band.core.protocols import AgentToolsProtocol
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AgentInput, PlatformMessage
from band.platform.event import PlatformEvent
from band.preprocessing.default import DefaultPreprocessor
from band.runtime.execution import ExecutionContext


async def no_execution_update() -> None:
    pass


@dataclass(frozen=True)
class ContextSnapshot:
    bootstrap: bool
    participants: str | None
    contacts: str | None


class RecordingPreprocessor(DefaultPreprocessor):
    def __init__(self) -> None:
        self.commits = 0

    async def process(
        self, ctx: ExecutionContext, event: PlatformEvent, agent_id: str
    ) -> AgentInput | None:
        inp = await super().process(ctx, event, agent_id)
        if inp is None:
            return None
        original = inp.commit_context

        def commit() -> None:
            self.commits += 1
            if original is not None:
                original()

        return replace(inp, commit_context=commit)


class FailingConverter:
    def __init__(self, adapter: HandoffAdapter) -> None:
        self.adapter = adapter

    def set_agent_name(self, name: str) -> None:
        pass

    def convert(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.adapter.phase == "conversion" and self.adapter.error is not None:
            raise self.adapter.error
        return raw


class HandoffAdapter(SimpleAdapter[list[dict[str, Any]]]):
    def __init__(
        self, phase: str = "setup", error: BaseException | None = None
    ) -> None:
        super().__init__(history_converter=FailingConverter(self))
        self.phase = phase
        self.error = error
        self.received: list[ContextSnapshot] = []
        self.during_execution: Callable[[], Awaitable[None]] = no_execution_update

    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: list[dict[str, Any]],
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        self.received.append(
            ContextSnapshot(is_session_bootstrap, participants_msg, contacts_msg)
        )
        with context_handoff() as handoff:
            if self.phase == "setup" and self.error is not None:
                raise self.error
            handoff.consume()
            await self.during_execution()
            if self.error is not None:
                raise self.error
            tools.turn.settle()


class UnclassifiedAdapter(HandoffAdapter):
    """An adapter that has not adopted explicit setup handoff classification."""

    async def on_event(self, inp: AgentInput) -> None:
        await self.during_execution()
        if self.error is not None:
            raise self.error
        inp.tools.turn.settle()
