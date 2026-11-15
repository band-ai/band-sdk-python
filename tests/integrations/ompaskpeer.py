"""Native ask dialogs and shutdown callbacks over a real JSONL subprocess."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
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
from tests.integrations.ompframes import Frame, send
from tests.integrations.omppeers.startup import StartupPeer

ASK_REQUEST_ID = "ask-1"
ASK_QUESTION_IDS = ("storage", "targets")
RESUMED_REPLY = "The answer resumed this session."
REFUSED_DIALOG = "refused-dialog"
DATABASE_QUESTION = "Database?"
DATABASE_HEADER = "Storage"
DATABASE_OPTIONS = ("SQLite", "Postgres")
DATABASE_DESCRIPTION = "Embedded"
DEPLOY_QUESTION = "Deploy where?"
DEPLOY_OPTIONS = ("Dev", "Prod")
PREVIEW_ONLY = "preview-only"
PREVIEW_QUESTION = "Which patch?"
PREVIEW_CHOICES = (
    ("A", "delete all records\n  DROP TABLE records;"),
    ("B", "preserve all records\n  SELECT * FROM records;"),
)


class AskPeer(StartupPeer):
    def __init__(self, scenario: str, plugin: Path | None = None) -> None:
        super().__init__(plugin)
        self.ask_scenario = scenario

    def ask(self, request_id: str = ASK_REQUEST_ID) -> None:
        questions: list[JsonValue] = [
            {
                "id": ASK_QUESTION_IDS[0],
                "question": DATABASE_QUESTION,
                "header": DATABASE_HEADER,
                "options": [
                    {
                        "label": DATABASE_OPTIONS[0],
                        "description": DATABASE_DESCRIPTION,
                        "preview": "CREATE TABLE example (id INTEGER);",
                    },
                    {"label": DATABASE_OPTIONS[1]},
                ],
                "recommended": 0,
            },
            {
                "id": ASK_QUESTION_IDS[1],
                "question": DEPLOY_QUESTION,
                "options": [{"label": label} for label in DEPLOY_OPTIONS],
                "multi": True,
            },
        ]
        if self.ask_scenario == PREVIEW_ONLY:
            questions = [
                {
                    "id": "patch",
                    "question": PREVIEW_QUESTION,
                    "options": [
                        {"label": label, "preview": preview}
                        for label, preview in PREVIEW_CHOICES
                    ],
                    "recommended": 0,
                }
            ]
        send(
            {
                "type": OmpFrameType.UI_REQUEST,
                "id": request_id,
                "method": OmpUIMethod.ASK,
                "questions": questions,
                "timeout": 30000,
            }
        )

    def run_prompt(self, frame: Frame) -> None:
        self.prompt_id = frame.id
        self.prompt_count += 1
        self.settled = False
        self.held = frame
        send({"type": OmpEventType.AGENT_START})
        if self.ask_scenario in {"ask-on-abort", "tool-on-abort"}:
            self.respond(frame)
            self.held = None
            self.emit_assistant_turn("waiting", pending=True)
            self.send_prompt_result(settled=False)
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
                enabled = self.ask_scenario != REFUSED_DIALOG
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
        self.emit_assistant_turn("")
        self.send_prompt_result(settled=True)
        self.settle_session()

    def _send_shutdown_callback(self) -> None:
        match self.ask_scenario:
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
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("--plugin-dir", type=Path)
    args, _native_options = parser.parse_known_args()
    peer = AskPeer(args.scenario, args.plugin_dir)
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
