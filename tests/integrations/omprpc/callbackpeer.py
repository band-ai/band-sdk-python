"""Real OMP peer controls for callback cancellation and same-ID replacement."""

from __future__ import annotations

import argparse
import sys
from enum import StrEnum
from pathlib import Path

from band.integrations.ompwire import OmpCommandName, OmpFrameType
from tests.integrations.omppeer import COMPLETE, Frame, Peer, announce_ready, send

CALLBACK_ID = "reused-callback"
OLD_RESULT = "old callback result"
NEW_RESULT = "new callback result"
CANCEL = "cancel-callback"
REPLACE = "replace-callback"
FINISH = "finish-callbacks"
HOLD_ACCEPTANCE = "hold-prompt-acceptance"
RELEASE_ACCEPTANCE = "release-prompt-acceptance"


class CallbackKind(StrEnum):
    HOST = "host"
    UI = "ui"


class CallbackPeer(Peer):
    def __init__(self, kind: CallbackKind) -> None:
        super().__init__("callback-identity")
        self.kind = kind
        self.hold_acceptance = False
        self.held_prompt: Frame | None = None

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
                if self.hold_acceptance:
                    self.held_prompt = frame
                else:
                    self.respond(frame)
                self.callback()
            case command if command == HOLD_ACCEPTANCE:
                self.hold_acceptance = True
                self.respond(frame)
            case command if command == RELEASE_ACCEPTANCE:
                if self.held_prompt is not None:
                    self.respond(self.held_prompt)
                    self.held_prompt = None
                self.respond(frame)
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
    parser.add_argument("--transcript", type=Path)
    args = parser.parse_args()
    peer = CallbackPeer(args.kind)
    announce_ready(peer.scenario)
    for line in sys.stdin:
        if args.transcript is not None:
            with args.transcript.open("a", encoding="utf-8") as transcript:
                transcript.write(line)
        peer.handle(Frame.model_validate_json(line, strict=True))


if __name__ == "__main__":
    main()
