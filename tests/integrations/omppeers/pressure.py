"""Hold native stdin while an independent control releases completion evidence."""

from __future__ import annotations

import socket
import threading

from tests.integrations.ompframes import Frame
from tests.integrations.omppeers.base import COMPLETE, RpcPeer
from tests.integrations.ompscenarios import PeerCommand, PeerScenario

PRESSURE_READY = b"ready\n"
PRESSURE_RELEASE = b"release\n"


class PressurePeer(RpcPeer):
    SCENARIOS = frozenset({PeerScenario.COMPLETION_BACKPRESSURE})

    def __init__(self, scenario: PeerScenario) -> None:
        super().__init__()

    def prompt(self, frame: Frame) -> None:
        self.begin_prompt(frame)
        self.start_agent_prompt(frame)

    def handle_command(self, frame: Frame) -> None:
        if frame.type != PeerCommand.HOLD:
            super().handle_command(frame)
            return
        port = frame.model_extra.get("port") if frame.model_extra else None
        if not isinstance(port, int):
            raise ValueError("Pressure peer requires a control port")
        self.respond(frame)
        with socket.create_connection(("127.0.0.1", port)) as control:
            control.sendall(PRESSURE_READY)
            with control.makefile("rb") as incoming:
                if incoming.readline() != PRESSURE_RELEASE:
                    raise ValueError("Unexpected pressure control")
            self.complete_agent_turn(COMPLETE)
            threading.Event().wait()
