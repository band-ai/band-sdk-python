"""Real OMP peer controls for callback cancellation and same-ID replacement."""

from __future__ import annotations

import argparse
import sys
from enum import StrEnum

from band.integrations.ompwire import OmpCommandName, OmpFrameType
from tests.integrations.omppeer import COMPLETE, Frame, Peer, announce_ready, send

CALLBACK_ID = "reused-callback"
OLD_RESULT = "old callback result"
NEW_RESULT = "new callback result"
CANCEL = "cancel-callback"
REPLACE = "replace-callback"
FINISH = "finish-callbacks"


class CallbackKind(StrEnum):
    HOST = "host"
    UI = "ui"


class CallbackPeer(Peer):
    def __init__(self, kind: CallbackKind) -> None:
        super().__init__("callback-identity")
        self.kind = kind

    def callback(self) -> None:
        match self.kind:
            case CallbackKind.HOST:
                send(
                    {
                        "type": OmpFrameType.HOST_TOOL_CALL,
                        "id": CALLBACK_ID,
                        "toolCallId": CALLBACK_ID,
                        "toolName": "band_chat",
                        "arguments": {},
                    }
                )
            case CallbackKind.UI:
                send(
                    {
                        "type": OmpFrameType.UI_REQUEST,
                        "id": CALLBACK_ID,
                        "method": "input",
                        "title": "Callback identity",
                    }
                )

    def cancel(self) -> None:
        match self.kind:
            case CallbackKind.HOST:
                send(
                    {
                        "type": OmpFrameType.HOST_TOOL_CANCEL,
                        "id": CANCEL,
                        "targetId": CALLBACK_ID,
                    }
                )
            case CallbackKind.UI:
                send(
                    {
                        "type": OmpFrameType.UI_REQUEST,
                        "id": CANCEL,
                        "method": "cancel",
                        "targetId": CALLBACK_ID,
                    }
                )

    def handle(self, frame: Frame) -> None:
        match frame.type:
            case OmpCommandName.PROMPT if self.prompt_count == 0:
                self.prompt_count += 1
                self.prompt_id = frame.id
                self.settled = False
                self.respond(frame)
                self.callback()
            case command if command == CANCEL:
                self.cancel()
                self.respond(frame)
            case command if command == REPLACE:
                self.callback()
                self.respond(frame)
            case command if command == FINISH:
                self.end(COMPLETE)
                self.result(settled=True)
                self.settle()
                self.respond(frame)
            case _:
                super().handle(frame)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", type=CallbackKind)
    args = parser.parse_args()
    peer = CallbackPeer(args.kind)
    announce_ready(peer.scenario)
    for line in sys.stdin:
        peer.handle(Frame.model_validate_json(line, strict=True))


if __name__ == "__main__":
    main()
