"""An in-memory Parlant session service, answering as a scripted engine would;
imports parlant, so only the parlant probe loads it."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import SimpleNamespace, TracebackType
from typing import Any, Self

from parlant.core.application import Application
from parlant.core.background_tasks import BackgroundTaskService
from parlant.core.loggers import StdoutLogger
from parlant.core.sessions import EventKind, EventSource
from parlant.core.tracer import LocalTracer

SESSION_ID = "probe-session"
AGENT_ID = "probe-agent"
CUSTOMER_ID = "probe-customer"


@dataclass(frozen=True)
class SessionEvent:
    """The attributes of a Parlant message event the adapter reads."""

    offset: int
    source: EventSource
    data: Mapping[str, Any]
    scripted: bool = False

    @property
    def message(self) -> str:
        return self.data["message"]


class ParlantPeer:
    """The session service the adapter drives, with Parlant's engine scripted.

    A customer message that triggers processing is answered at once with
    ``reply`` as an AI-agent message, as the engine does when it finishes.
    ``engine_input`` is everything else in the session: what the engine reads.
    """

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self._events: list[SessionEvent] = []
        self.background_tasks = BackgroundTaskService(StdoutLogger(LocalTracer()))
        self.agent: Any = SimpleNamespace(id=AGENT_ID)
        self.server: Any = SimpleNamespace(
            container={
                Application: SimpleNamespace(sessions=self),
                BackgroundTaskService: self.background_tasks,
            },
            create_customer=self.create_customer,
        )

    async def __aenter__(self) -> Self:
        await self.background_tasks.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        await self.background_tasks.cancel_all(reason="Probe completed")
        return await self.background_tasks.__aexit__(exc_type, exc_value, traceback)

    @property
    def engine_input(self) -> str:
        return "\n".join(e.message for e in self._events if not e.scripted)

    async def create_customer(self, *, name: str, id: str) -> Any:
        return SimpleNamespace(id=CUSTOMER_ID)

    async def create(self, customer_id: str, agent_id: str, title: str) -> Any:
        return SimpleNamespace(id=SESSION_ID)

    async def create_customer_message(
        self,
        session_id: str,
        moderation: Any,
        message: str,
        source: EventSource,
        trigger_processing: bool,
        metadata: Mapping[str, Any] | None,
    ) -> SessionEvent:
        event = self._append(source, {"message": message})
        if trigger_processing:
            self._append(EventSource.AI_AGENT, {"message": self._reply}, scripted=True)
        return event

    async def create_event(
        self,
        session_id: str,
        kind: EventKind,
        data: Mapping[str, Any],
        metadata: Mapping[str, Any] | None,
        source: EventSource,
        trigger_processing: bool,
    ) -> SessionEvent:
        return self._append(source, data)

    async def wait_for_more_events(
        self,
        session_id: str,
        min_offset: int,
        kinds: Sequence[EventKind],
        source: EventSource,
        timeout: Any,
    ) -> bool:
        return bool(self._from(min_offset, source))

    async def find_events(
        self,
        session_id: str,
        min_offset: int,
        source: EventSource,
        kinds: Sequence[EventKind],
        trace_id: str | None,
    ) -> list[SessionEvent]:
        return self._from(min_offset, source)

    def _append(
        self, source: EventSource, data: Mapping[str, Any], *, scripted: bool = False
    ) -> SessionEvent:
        event = SessionEvent(
            offset=len(self._events), source=source, data=data, scripted=scripted
        )
        self._events.append(event)
        return event

    def _from(self, min_offset: int, source: EventSource) -> list[SessionEvent]:
        return [
            e for e in self._events if e.offset >= min_offset and e.source == source
        ]
