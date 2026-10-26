"""Expose independent native busy signals and interrupted idle observations."""

from __future__ import annotations

from pydantic import JsonValue

from band.integrations.ompwire import OmpEventType
from tests.integrations.ompframes import Frame, send
from tests.integrations.omppeers.base import COMPLETE, RpcPeer
from tests.integrations.ompscenarios import PeerCommand, PeerScenario

BUSY_FIELDS = {
    PeerScenario.BUSY_STREAMING: "isStreaming",
    PeerScenario.BUSY_COMPACTION: "isCompacting",
    PeerScenario.BUSY_QUEUED: "queuedMessageCount",
    PeerScenario.BUSY_ASYNC: "hasPendingAsyncWork",
}


class IdlePeer(RpcPeer):
    SCENARIOS = frozenset((*BUSY_FIELDS, PeerScenario.REARM_COMPLETION))

    def __init__(self, scenario: PeerScenario) -> None:
        super().__init__()
        self.scenario = scenario
        self.busy = True

    def prompt(self, frame: Frame) -> None:
        self.begin_prompt(frame)
        self.start_agent_prompt(frame)
        self.emit_assistant_turn(COMPLETE)
        self.settled = True
        if self.scenario != PeerScenario.REARM_COMPLETION:
            # Fault injection: settlement claims idle while one busy signal remains.
            self.send_prompt_result(settled=True)

    def state_snapshot(self) -> dict[str, JsonValue]:
        state = super().state_snapshot()
        if self.scenario in BUSY_FIELDS and self.busy:
            state[BUSY_FIELDS[self.scenario]] = (
                1 if self.scenario == PeerScenario.BUSY_QUEUED else True
            )
        return state

    def handle_command(self, frame: Frame) -> None:
        match frame.type:
            case PeerCommand.REPEAT_SETTLEMENT:
                self.settle_session()
                self.respond(frame)
            case PeerCommand.RESUME_ACTIVITY:
                send({"type": OmpEventType.AGENT_START})
                self.emit_assistant_turn(COMPLETE)
                self.respond(frame)
            case PeerCommand.RELEASE_PROMPT:
                self.busy = False
                send({"type": OmpEventType.AGENT_START})
                self.complete_agent_turn(COMPLETE)
                self.respond(frame)
            case _:
                super().handle_command(frame)
