"""Hold prompts across process termination, restart and follow-up operations."""

from __future__ import annotations

from band.integrations.ompwire import OmpEventType
from tests.integrations.ompframes import Frame, send
from tests.integrations.omppeers.base import (
    BACKGROUND_COMPLETE,
    FOLLOW_UP_PROMPT,
    YIELDED,
    RpcPeer,
)
from tests.integrations.ompscenarios import PeerCommand, PeerScenario

CRASH_EXIT = 3


class ProcessLifecyclePeer(RpcPeer):
    SCENARIOS = frozenset({PeerScenario.LIFECYCLE})

    def __init__(self, scenario: PeerScenario) -> None:
        super().__init__()
        if scenario not in self.SCENARIOS:
            raise ValueError(f"Unsupported process lifecycle script: {scenario}")

    def prompt(self, frame: Frame) -> None:
        self.begin_prompt(frame)
        if frame.message == FOLLOW_UP_PROMPT:
            self.complete_prompt(frame)
            return
        self.start_agent_prompt(frame)
        self.emit_assistant_turn(YIELDED, pending=True)
        self.send_prompt_result(settled=False)

    def handle_command(self, frame: Frame) -> None:
        match frame.type:
            case PeerCommand.RELEASE_PROMPT:
                send({"type": OmpEventType.AGENT_START})
                self.emit_assistant_turn(BACKGROUND_COMPLETE)
                self.settle_session()
                self.respond(frame)
            case PeerCommand.EXIT_PROCESS:
                raise SystemExit(CRASH_EXIT)
            case _:
                super().handle_command(frame)
