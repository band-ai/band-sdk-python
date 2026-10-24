"""Native ask dialogs and shutdown callbacks over a real JSONL subprocess."""

from __future__ import annotations

import sys
from typing import cast

from pydantic import JsonValue

from band.integrations.ompwire import (
    OMP_FRAME_BYTES,
    OMP_NATIVE_PROTOCOL_VERSION,
    OMP_REASSEMBLED_BYTES,
    OmpCommandName,
    OmpEventType,
    OmpFrameType,
    OmpUIMethod,
)
from tests.integrations.omppeer import Frame, Peer, send

ASK_REQUEST_ID = "ask-1"
ASK_QUESTION_IDS = ("storage", "targets")
RESUMED_REPLY = "The answer resumed this session."
REFUSED_DIALOG = "refused-dialog"


class AskPeer(Peer):
    @staticmethod
    def ask(request_id: str = ASK_REQUEST_ID) -> None:
        send(
            {
                "type": OmpFrameType.UI_REQUEST,
                "id": request_id,
                "method": OmpUIMethod.ASK,
                "questions": [
                    {
                        "id": ASK_QUESTION_IDS[0],
                        "question": "Database?",
                        "header": "Storage",
                        "options": [
                            {
                                "label": "SQLite",
                                "description": "Embedded",
                                "preview": "CREATE TABLE example (id INTEGER);",
                            },
                            {"label": "Postgres"},
                        ],
                        "recommended": 0,
                    },
                    {
                        "id": ASK_QUESTION_IDS[1],
                        "question": "Deploy where?",
                        "options": [{"label": "Dev"}, {"label": "Prod"}],
                        "multi": True,
                    },
                ],
                "timeout": 30000,
            }
        )

    def prompt(self, frame: Frame) -> None:
        self.prompt_id = frame.id
        self.prompt_count += 1
        self.settled = False
        self.held = frame
        send({"type": OmpEventType.AGENT_START})
        if self.scenario in {"ask-on-abort", "tool-on-abort"}:
            self.respond(frame)
            self.held = None
            self.end("waiting", pending=True)
            self.result(settled=False)
            return
        if self.prompt_count == 1:
            self.ask()
        else:
            send(
                {
                    "type": OmpFrameType.HOST_TOOL_CALL,
                    "id": "resumed-reply",
                    "toolCallId": "reply-2",
                    "toolName": "band_send_message",
                    "arguments": {
                        "content": RESUMED_REPLY,
                        "mentions": ["peer-456"],
                    },
                }
            )

    def handle(self, frame: Frame) -> None:
        if frame.type in {
            OmpCommandName.SET_ASK_DIALOG,
            OmpFrameType.UI_RESPONSE,
            OmpFrameType.HOST_TOOL_RESULT,
        }:
            self.frames.append(
                cast(dict[str, JsonValue], frame.model_dump(exclude_unset=True))
            )
        match frame.type:
            case OmpCommandName.SET_ASK_DIALOG:
                enabled = self.scenario != REFUSED_DIALOG
                self.respond(frame, {"enabled": enabled})
                return
            case OmpFrameType.UI_RESPONSE | OmpFrameType.HOST_TOOL_RESULT:
                self._complete_prompt()
                return
            case OmpCommandName.ABORT:
                self._send_shutdown_callback()
        super().handle(frame)

    def _complete_prompt(self) -> None:
        if self.held is None:
            return
        self.respond(self.held)
        self.held = None
        self.end("")
        self.result(settled=True)
        self.settle()

    def _send_shutdown_callback(self) -> None:
        match self.scenario:
            case "ask-on-abort":
                self.ask("late-ask")
            case "tool-on-abort":
                send(
                    {
                        "type": OmpFrameType.HOST_TOOL_CALL,
                        "id": "late-tool",
                        "toolCallId": "call-late",
                        "toolName": "band_send_message",
                        "arguments": {"content": "Must never reach the room"},
                    }
                )


def main() -> None:
    peer = AskPeer(sys.argv[1])
    send(
        {
            "type": OmpFrameType.READY,
            "protocolVersion": 1,
            "supportedProtocolVersions": [1, OMP_NATIVE_PROTOCOL_VERSION],
            "maxFrameBytes": OMP_FRAME_BYTES,
            "maxReassembledFrameBytes": OMP_REASSEMBLED_BYTES,
        }
    )
    for line in sys.stdin:
        peer.handle(Frame.model_validate_json(line, strict=True))


if __name__ == "__main__":
    main()
