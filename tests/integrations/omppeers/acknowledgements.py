"""Inject missing command acknowledgements after otherwise complete prompts."""

from __future__ import annotations

from band.integrations.ompwire import OmpEventType, OmpPromptStatus
from band.runtime.tools.types import BandTool
from tests.integrations.ompframes import Frame, send, send_host_tool_request
from tests.integrations.omppeers.base import COMPLETE, RpcPeer
from tests.integrations.ompscenarios import PeerCommand, PeerScenario

STOP_CALL_ID = "stop-tool"


class AcknowledgementPeer(RpcPeer):
    SCENARIOS = frozenset({PeerScenario.LOST_PROMPT_ACK, PeerScenario.LOST_ABORT_ACK})

    def __init__(self, scenario: PeerScenario) -> None:
        super().__init__()
        self.scenario = scenario
        self.acknowledgement: Frame | None = None

    def prompt(self, frame: Frame) -> None:
        self.begin_prompt(frame)
        if self.is_follow_up(frame):
            self.complete_prompt(frame)
        elif self.scenario == PeerScenario.LOST_PROMPT_ACK:
            self.acknowledgement = frame
            send({"type": OmpEventType.AGENT_START})
            self.complete_agent_turn(COMPLETE)
        else:
            self.start_agent_prompt(frame)
            send_host_tool_request(STOP_CALL_ID, STOP_CALL_ID, BandTool.NO_REPLY, {})

    def abort_prompt(self, frame: Frame) -> None:
        if self.scenario != PeerScenario.LOST_ABORT_ACK:
            super().abort_prompt(frame)
            return
        self.acknowledgement = frame
        self.emit_assistant_turn(COMPLETE, stop=OmpPromptStatus.ABORTED)
        self.send_prompt_result(settled=True, status=OmpPromptStatus.ABORTED)
        self.settle_session()

    def handle_command(self, frame: Frame) -> None:
        if frame.type == PeerCommand.RELEASE_ACK:
            if self.acknowledgement is None:
                raise ValueError("No command acknowledgement is held")
            self.respond(self.acknowledgement)
            self.acknowledgement = None
            self.respond(frame)
            return
        super().handle_command(frame)
