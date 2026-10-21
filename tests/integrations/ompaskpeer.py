"""Native ask dialogs and shutdown callbacks over a real JSONL subprocess."""

from __future__ import annotations

import sys
from typing import cast

from pydantic import JsonValue

from tests.integrations.omppeer import Frame, Peer, send


class AskPeer(Peer):
    def __init__(self, scenario: str) -> None:
        super().__init__(scenario)
        self.dialog_enabled = False

    @staticmethod
    def ask(request_id: str = "ask-1") -> None:
        send(
            {
                "type": "extension_ui_request",
                "id": request_id,
                "method": "ask",
                "questions": [
                    {
                        "id": "storage",
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
                        "id": "targets",
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
        send({"type": "agent_start"})
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
                    "type": "host_tool_call",
                    "id": "resumed-reply",
                    "toolCallId": "reply-2",
                    "toolName": "band_send_message",
                    "arguments": {
                        "content": "The answer resumed this session.",
                        "mentions": ["peer-456"],
                    },
                }
            )

    def handle(self, frame: Frame) -> None:
        if frame.type == "set_ask_dialog":
            self.frames.append(
                cast(dict[str, JsonValue], frame.model_dump(exclude_unset=True))
            )
            self.dialog_enabled = self.scenario != "refused-dialog"
            self.respond(frame, {"enabled": self.dialog_enabled})
            return
        if frame.type in {"extension_ui_response", "host_tool_result"}:
            self.frames.append(
                cast(dict[str, JsonValue], frame.model_dump(exclude_unset=True))
            )
            if self.held is not None:
                self.respond(self.held)
                self.held = None
                self.end("")
                self.result(settled=True)
                self.settle()
            return
        if frame.type == "abort":
            if self.scenario == "ask-on-abort":
                self.ask("late-ask")
            if self.scenario == "tool-on-abort":
                send(
                    {
                        "type": "host_tool_call",
                        "id": "late-tool",
                        "toolCallId": "call-late",
                        "toolName": "band_send_message",
                        "arguments": {"content": "Must never reach the room"},
                    }
                )
        super().handle(frame)


def main() -> None:
    peer = AskPeer(sys.argv[1])
    send(
        {
            "type": "ready",
            "protocolVersion": 1,
            "supportedProtocolVersions": [1, 2],
            "maxFrameBytes": 1024 * 1024,
            "maxReassembledFrameBytes": 64 * 1024 * 1024,
        }
    )
    for line in sys.stdin:
        peer.handle(Frame.model_validate_json(line, strict=True))


if __name__ == "__main__":
    main()
