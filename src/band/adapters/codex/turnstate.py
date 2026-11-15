"""State of one Band turn driving a native Codex turn."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from band.adapters.codex.rpc import (
    CodexClientProtocol,
    delegated_threads,
    event_thread_id,
    event_turn_id,
)
from band.core.protocols import AgentToolsProtocol
from band.core.turn_lifecycle import TurnTask
from band.integrations.codex import RpcEvent
from band.integrations.codex.types import CodexTurnStatus


class CodexContinuityError(RuntimeError):
    """A retained Codex thread cannot continue as it was; its id is kept."""


@dataclass(frozen=True)
class CodexTurn:
    """What interrupting one room's running app-server turn needs."""

    client: CodexClientProtocol
    thread_id: str
    turn_id: str


# How a turn whose Codex process was lost reports its failure.
TRANSPORT_LOST_ERROR = "Codex transport closed unexpectedly"


# How a turn that failed for an unexpected reason reports its failure.
TURN_PROCESSING_ERROR = "Internal error during turn processing"


# How a turn reports a room process that did not start or stop within
# ``retention.transition_timeout_s``.
PROCESS_NOT_READY_ERROR = "Codex process did not become ready in time"


@dataclass
class NativeMessage:
    """One agent-message item's streamed and authoritative completed text."""

    phase: str | None = None
    streamed_text: str = ""
    completed_text: str | None = None

    @property
    def unstreamed_text(self) -> str:
        text = self.completed_text or ""
        if text.startswith(self.streamed_text):
            return text[len(self.streamed_text) :]
        return text


@dataclass
class TurnResult:
    """Aggregated result from processing a single Codex turn's event stream."""

    native_messages: dict[str, NativeMessage] = dataclass_field(default_factory=dict)
    turn_status: str = CodexTurnStatus.FAILED
    turn_error: str = ""

    @property
    def completed(self) -> bool:
        """Whether the turn completed: only then is a seeding turn's thread seeded.

        An interrupted seed is abandoned like a failed one.
        """
        return self.turn_status == CodexTurnStatus.COMPLETED


def audience_sender_ids(audience: Sequence[dict[str, str]]) -> list[str]:
    """Project the current turn audience onto sender IDs."""
    return [person["id"] for person in audience]


@dataclass
class NativeTurn:
    """The Band turn that started one native turn, and the events addressed to it.

    Codex names the turn only when ``turn/start`` answers, so events that
    arrive first wait in ``early`` until ``turn_id`` is known. Sub-agents
    the turn delegates to ask under their own thread and turn, and Codex
    names their thread only in the turn's own items, so their requests
    wait in ``unattributed`` until one does.
    """

    client: CodexClientProtocol
    generation: int
    owner: TurnTask
    tools: AgentToolsProtocol | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    # An exception is the reader's own failure, which ends the turn.
    events: asyncio.Queue[RpcEvent | Exception] = dataclass_field(
        default_factory=asyncio.Queue
    )
    early: list[RpcEvent] = dataclass_field(default_factory=list)
    sub_agents: set[str] = dataclass_field(default_factory=set)
    unattributed: list[RpcEvent] = dataclass_field(default_factory=list)
    # Why the process stopped -- its closing notice or the reader's failure,
    # queued behind its requests.
    loss: RpcEvent | Exception | None = None

    @property
    def lost(self) -> bool:
        return self.loss is not None

    def owns(self, event: RpcEvent) -> bool:
        # Every event a turn handles names its turn; thread-scoped ones do not.
        if event_turn_id(event) == self.turn_id:
            return True
        # Only a sub-agent's requests: its own turn/completed must not end this turn.
        return event.is_request and event_thread_id(event) in self.sub_agents

    def may_own(self, event: RpcEvent) -> bool:
        """A request from another thread, which a later collab item may name."""
        return event.is_request and event_thread_id(event) not in (None, self.thread_id)

    def deliver(self, event: RpcEvent) -> None:
        """Queue an owned event, then the held requests it names a sub-agent for."""
        self.events.put_nowait(event)
        if not (receivers := delegated_threads(event) - self.sub_agents):
            return
        self.sub_agents |= receivers
        held, self.unattributed = self.unattributed, []
        for request in held:
            if self.owns(request):
                self.events.put_nowait(request)
            else:
                self.unattributed.append(request)

    def claim(self, event: RpcEvent) -> bool:
        """Deliver or hold ``event``; False when this turn can never own it."""
        if self.owns(event):
            self.deliver(event)
        elif self.may_own(event):
            self.unattributed.append(event)
        else:
            return False
        return True


def undelivered_events(native: NativeTurn) -> list[RpcEvent]:
    """Events still waiting for the native turn when its Band turn ends."""
    queued = [native.events.get_nowait() for _ in range(native.events.qsize())]
    return [
        *native.early,
        *(item for item in queued if isinstance(item, RpcEvent)),
        *native.unattributed,
    ]
