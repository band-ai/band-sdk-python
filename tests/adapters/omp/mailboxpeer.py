"""Control native callbacks and observe their ordering through real JSONL RPC."""

from __future__ import annotations

import sys
from enum import StrEnum
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from band.integrations.omp import OMP_FORM_APPROVE, OMP_FORM_DENY
from band.integrations.ompprocess import OMP_MCP_RECEIPT, omp_mcp_ready_command
from band.integrations.omprpc.prompt import OmpCallbackKind
from band.integrations.ompwire import (
    OmpCommandName,
    OmpEventType,
    OmpFrameType,
    OmpUIMethod,
)
from band.runtime.tools import BandTool
from tests.integrations.omppeer import Frame, Peer, announce_ready, send

MODEL_PROVIDER = "openai"
ORIGINAL_MODEL = "original"
CALLBACK_MODEL = "callback"
SECOND_MODEL = "second-callback"
PUBLIC_MODEL = "public"
REJECTED_MODEL = "rejected"
UNSUPPORTED_EFFORT = "extreme"
FIRST_CALLBACK = "permission-first"
SECOND_CALLBACK = "permission-second"
TOOL_CALLBACK = "tool-held"
REPLY_CALLBACK = "mailbox-reply"


class PeerCommand(StrEnum):
    PERMISSION = "mailbox-permission"
    TOOL = "mailbox-tool"
    CANCEL_CALLBACK = "mailbox-cancel"
    FINISH = "mailbox-finish"
    HOLD_MODEL_RESPONSE = "mailbox-hold-model-response"
    WAIT_MODEL_RESPONSE = "mailbox-wait-model-response"


def model_reply(model: str) -> str:
    return f"Native model: {MODEL_PROVIDER}/{model}"


class MailboxPeer(Peer):
    def __init__(self) -> None:
        super().__init__("mailbox")
        self.callback_turn = int(sys.argv[1])
        self.callback_count = int(sys.argv[2])
        self.initial_tool = sys.argv[4] == OmpFrameType.HOST_TOOL_CALL
        self.hold_model: str | None = None
        self.held_model_request: Frame | None = None
        self.model_waiter: Frame | None = None
        self.model = ORIGINAL_MODEL
        self.pending: set[str] = set()
        self.plugin = (
            Path(sys.argv[sys.argv.index("--plugin-dir") + 1])
            if "--plugin-dir" in sys.argv
            else None
        )

    def emit(self, frame: dict[str, JsonValue]) -> None:
        self.frames.append(frame)
        send(frame)

    def permission(self, callback_id: str) -> None:
        self.pending.add(callback_id)
        self.emit(
            {
                "type": OmpFrameType.UI_REQUEST,
                "id": callback_id,
                "method": OmpUIMethod.SELECT,
                "title": callback_id,
                "options": [OMP_FORM_APPROVE, OMP_FORM_DENY],
            }
        )

    def tool(self, callback_id: str) -> None:
        self.pending.add(callback_id)
        self.emit(
            {
                "type": OmpFrameType.HOST_TOOL_CALL,
                "id": callback_id,
                "toolCallId": callback_id,
                "toolName": BandTool.GET_PARTICIPANTS,
                "arguments": {},
            }
        )

    def complete(self) -> None:
        self.emit(
            {
                "type": OmpFrameType.HOST_TOOL_CALL,
                "id": REPLY_CALLBACK,
                "toolCallId": REPLY_CALLBACK,
                "toolName": BandTool.SEND_MESSAGE,
                "arguments": {
                    "content": model_reply(self.model),
                    "mentions": ["@alice"],
                },
            }
        )

    def finish_reply(self) -> None:
        self.end(model_reply(self.model))
        self.result(settled=True)
        self.settle()

    def prompt(self, frame: Frame) -> None:
        self.prompt_id = frame.id
        self.settled = False
        self.respond(frame)
        if self.plugin is not None and frame.message == (
            f"/{omp_mcp_ready_command(str(self.plugin))}"
        ):
            (self.plugin / OMP_MCP_RECEIPT).write_text(
                '{"systemPromptReady":true,"servers":[]}', encoding="utf-8"
            )
            self.result(settled=True)
            self.settle()
            return
        self.prompt_count += 1
        send({"type": OmpEventType.AGENT_START})
        if self.prompt_count == self.callback_turn and self.callback_count:
            for callback_id in (FIRST_CALLBACK, SECOND_CALLBACK)[: self.callback_count]:
                if self.initial_tool:
                    self.tool(callback_id)
                else:
                    self.permission(callback_id)
        else:
            self.complete()

    def handle(self, frame: Frame) -> None:
        match frame.type:
            case OmpCommandName.SET_MODEL:
                self.frames.append(frame.model_dump(exclude_unset=True))
                self.model = str((frame.model_extra or {})["modelId"])
                if self.model == self.hold_model:
                    self.held_model_request = frame
                    if self.model_waiter is not None:
                        self.respond(self.model_waiter)
                        self.model_waiter = None
                else:
                    self.respond(frame)
            case OmpCommandName.GET_AVAILABLE_THINKING_LEVELS:
                self.respond(frame, {"levels": ["low", "high"]})
            case PeerCommand.HOLD_MODEL_RESPONSE:
                self.hold_model = str((frame.model_extra or {})["modelId"])
                self.held_model_request = None
                self.respond(frame)
            case PeerCommand.WAIT_MODEL_RESPONSE:
                if self.held_model_request is not None:
                    self.respond(frame)
                else:
                    self.model_waiter = frame
            case OmpCommandName.GET_STATE:
                self.frames.append(frame.model_dump(exclude_unset=True))
                self.respond(
                    frame,
                    {
                        "sessionId": "mailbox-session",
                        "model": {
                            "id": self.model,
                            "provider": MODEL_PROVIDER,
                            "name": self.model,
                        },
                        "isSettled": self.settled,
                        "isStreaming": False,
                        "hasPendingAsyncWork": not self.settled,
                        "queuedMessageCount": 0,
                    },
                )
            case OmpCommandName.GET_AVAILABLE_COMMANDS:
                self.respond(
                    frame,
                    {"commands": [{"name": omp_mcp_ready_command(str(self.plugin))}]},
                )
            case PeerCommand.PERMISSION | PeerCommand.TOOL:
                callback_id = str((frame.model_extra or {})["callbackId"])
                if frame.type == PeerCommand.PERMISSION:
                    self.permission(callback_id)
                else:
                    self.tool(callback_id)
                self.respond(frame)
            case PeerCommand.CANCEL_CALLBACK:
                extra = frame.model_extra or {}
                callback_id = str(extra["targetId"])
                self.pending.discard(callback_id)
                self.emit(
                    {
                        "type": OmpFrameType.UI_REQUEST,
                        "id": "cancel-ui",
                        "method": OmpUIMethod.CANCEL,
                        "targetId": callback_id,
                    }
                    if extra.get("kind") == OmpCallbackKind.UI
                    else {
                        "type": OmpFrameType.HOST_TOOL_CANCEL,
                        "id": "cancel-host",
                        "targetId": callback_id,
                    }
                )
                self.respond(frame)
            case PeerCommand.FINISH:
                self.complete()
                self.respond(frame)
            case OmpFrameType.UI_RESPONSE | OmpFrameType.HOST_TOOL_RESULT:
                self.frames.append(
                    cast(dict[str, JsonValue], frame.model_dump(exclude_unset=True))
                )
                if frame.id == REPLY_CALLBACK:
                    self.finish_reply()
                    return
                self.pending.discard(frame.id or "")
                if not self.pending:
                    self.complete()
            case _:
                super().handle(frame)


def main() -> None:
    announce_ready("mailbox")
    peer = MailboxPeer()
    for line in sys.stdin:
        if sys.argv[3]:
            with Path(sys.argv[3]).open("a", encoding="utf-8") as transcript:
                transcript.write(line)
        peer.handle(Frame.model_validate_json(line, strict=True))


if __name__ == "__main__":
    main()
