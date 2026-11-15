"""Drive adapter callbacks over actual native RPC frames."""

from __future__ import annotations

import argparse
import socket
import sys
from enum import StrEnum
from pathlib import Path

import psutil

from band.integrations.ompwire import (
    OmpEvent,
    OmpEventType,
    OmpFrameType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
)
from tests.integrations.ompframes import Frame, send
from tests.integrations.ompgate import ProcessReceipt
from tests.integrations.omppeer import announce_ready
from tests.integrations.omppeers.startup import StartupPeer
from tests.integrations.ompscenarios import PeerScenario


class TurnControl(StrEnum):
    EVENT = "adapter-event"
    TOOL = "adapter-host-tool"
    UI = "adapter-ui"
    FINISH = "adapter-finish"


class AdapterTurnPeer(StartupPeer):
    def __init__(self, plugin: Path | None, port: int) -> None:
        super().__init__(plugin)
        self.port = port
        self.pending: dict[tuple[str, str], Frame] = {}

    def run_prompt(self, frame: Frame) -> None:
        self.begin_prompt(frame)
        self.start_agent_prompt(frame)
        process = psutil.Process()
        receipt = ProcessReceipt(
            pid=process.pid, parent_pid=process.ppid(), created=process.create_time()
        )
        with socket.create_connection(("127.0.0.1", self.port)) as connection:
            connection.sendall((receipt.model_dump_json() + "\n").encode())

    def handle_command(self, frame: Frame) -> None:
        match frame.type:
            case TurnControl.EVENT:
                event = OmpEvent.model_validate(frame.event)
                send(event.model_dump(mode="json", exclude_none=True))
                self.respond(frame)
            case TurnControl.TOOL:
                call = OmpHostToolCall.model_validate(frame.call)
                self.pending[(OmpFrameType.HOST_TOOL_RESULT, call.id)] = frame
                send(call.model_dump(mode="json", exclude_none=True))
            case TurnControl.UI:
                request = OmpUIRequest.model_validate(frame.request)
                self.pending[(OmpFrameType.UI_RESPONSE, request.id)] = frame
                send(request.model_dump(mode="json", exclude_none=True))
            case OmpFrameType.HOST_TOOL_RESULT:
                if frame.id is None:
                    raise ValueError("Missing host tool result id")
                control = self.pending.pop((frame.type, frame.id))
                result = OmpToolResult.model_validate(
                    {**(frame.result or {}), "is_error": bool(frame.isError)}
                )
                self.respond(control, result.model_dump(mode="json"))
            case OmpFrameType.UI_RESPONSE:
                if frame.id is None:
                    raise ValueError("Missing UI response id")
                control = self.pending.pop((frame.type, frame.id))
                self.respond(control, frame.model_dump(mode="json", exclude_unset=True))
            case TurnControl.FINISH:
                send(
                    {
                        "type": OmpEventType.AGENT_END,
                        "messages": [],
                        "isTerminal": True,
                        "yielded": True,
                        "awaitingAsyncWork": False,
                    }
                )
                self.send_prompt_result(settled=True)
                self.settle_session()
                self.respond(frame)
            case _:
                super().handle_command(frame)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-dir", type=Path)
    parser.add_argument("--gate-port", type=int, required=True)
    args, _native_options = parser.parse_known_args()
    peer = AdapterTurnPeer(args.plugin_dir, args.gate_port)
    announce_ready(PeerScenario.STANDARD_TURN)
    for line in sys.stdin:
        peer.handle(Frame.model_validate_json(line, strict=True))


if __name__ == "__main__":
    main()
