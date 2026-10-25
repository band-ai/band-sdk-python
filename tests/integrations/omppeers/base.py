"""Provide shared JSONL RPC mechanics for focused OMP test peers."""

from __future__ import annotations

import os
from typing import cast

from pydantic import JsonValue

from band.integrations.ompwire import (
    OMP_NATIVE_PROTOCOL_VERSION,
    OmpCommandName,
    OmpEventType,
    OmpFrameType,
    OmpPromptStatus,
)
from tests.integrations.ompframes import (
    Frame,
    assistant_message,
    enable_chunked_frames,
    send,
)
from tests.integrations.ompscenarios import PeerCommand

COMPLETE = "complete"
YIELDED = "yielded"
BACKGROUND_COMPLETE = "background complete"
FOLLOW_UP_PROMPT = "second"


class RpcPeer:
    def __init__(self) -> None:
        self.settled = True
        self.state_requested = False
        self.prompt_id: str | None = None
        self.prompt_count = 0
        self.frames: list[dict[str, JsonValue]] = []
        self.held: Frame | None = None
        self.state_request_count = 0
        self.snapshot_responses = 0
        self.state_barriers: list[Frame] = []

    def begin_prompt(self, frame: Frame) -> None:
        self.prompt_id = frame.id
        self.prompt_count += 1
        self.settled = False
        self.state_requested = False

    def is_follow_up(self, frame: Frame) -> bool:
        return self.prompt_count > 1 or frame.message == FOLLOW_UP_PROMPT

    def prompt(self, frame: Frame) -> None:
        self.begin_prompt(frame)
        self.complete_prompt(frame)

    def checkpoint(self, frame: Frame) -> None:
        self.respond(
            frame,
            {
                "pid": os.getpid(),
                "frames": self.frames,
                "snapshots": self.snapshot_responses,
            },
        )

    def state_snapshot(self) -> dict[str, JsonValue]:
        return {
            "sessionId": "session-local",
            "sessionFile": "/local/session.jsonl",
            "model": {
                "id": "test-model",
                "provider": "test-provider",
                "name": "Test model",
            },
            "isStreaming": False,
            "hasPendingAsyncWork": not self.settled,
            "queuedMessageCount": 0,
            "isSettled": self.settled,
        }

    def respond_with_state(self, frame: Frame) -> None:
        self.state_requested = True
        self.state_request_count += 1
        self.snapshot_responses += 1
        self.respond(frame, self.state_snapshot())
        self.release_state_barriers()

    def release_state_barriers(self) -> None:
        observed = {
            PeerCommand.WAIT_FOR_SNAPSHOTS: self.snapshot_responses,
            PeerCommand.WAIT_FOR_STATE_REQUESTS: self.state_request_count,
        }
        pending = []
        for barrier in self.state_barriers:
            if observed[barrier.type] >= barrier.count:
                self.respond(barrier)
            else:
                pending.append(barrier)
        self.state_barriers = pending

    def negotiate_protocol(self, frame: Frame) -> None:
        self.respond(frame, {"protocolVersion": OMP_NATIVE_PROTOCOL_VERSION})
        enable_chunked_frames()

    def abort_prompt(self, frame: Frame) -> None:
        self.respond(frame)
        if self.prompt_id is not None:
            self.emit_assistant_turn(
                OmpPromptStatus.ABORTED, stop=OmpPromptStatus.ABORTED
            )
            self.settle_session()

    def handle(self, frame: Frame) -> None:
        self.frames.append(
            cast(dict[str, JsonValue], frame.model_dump(exclude_unset=True))
        )
        self.handle_command(frame)

    def handle_command(self, frame: Frame) -> None:
        match frame.type:
            case OmpCommandName.NEGOTIATE_PROTOCOL:
                self.negotiate_protocol(frame)
            case OmpCommandName.SET_ASK_DIALOG:
                self.respond(frame, {"enabled": True})
            case OmpCommandName.PROMPT:
                self.prompt(frame)
            case OmpCommandName.GET_STATE:
                self.respond_with_state(frame)
            case PeerCommand.CHECKPOINT:
                self.checkpoint(frame)
            case PeerCommand.WAIT_FOR_SNAPSHOTS | PeerCommand.WAIT_FOR_STATE_REQUESTS:
                self.state_barriers.append(frame)
                self.release_state_barriers()
            case OmpCommandName.ABORT:
                self.abort_prompt(frame)
            case OmpFrameType.HOST_TOOL_RESULT | OmpFrameType.UI_RESPONSE:
                return
            case OmpCommandName.SET_HOST_TOOLS:
                self.respond(frame)
            case _:
                raise ValueError(f"Unsupported peer command: {frame.type}")

    def respond(self, frame: Frame, data: dict[str, JsonValue] | None = None) -> None:
        result: dict[str, JsonValue] = {
            "type": OmpFrameType.RESPONSE,
            "id": frame.id,
            "command": frame.type,
            "success": True,
        }
        if data is not None:
            result["data"] = data
        send(result)

    def emit_assistant_turn(
        self,
        text: str,
        *,
        stop: str = "stop",
        pending: bool = False,
        compacted: bool = False,
    ) -> None:
        message = assistant_message(text, stop=stop)
        send({"type": OmpEventType.MESSAGE_END, "messageId": text, "message": message})
        send(
            {
                "type": OmpEventType.AGENT_END,
                "messages": [] if compacted else [message],
                "isTerminal": not pending,
                "yielded": True,
                "awaitingAsyncWork": pending,
            }
        )

    def send_prompt_result(self, *, settled: bool, status: str = "completed") -> None:
        result: dict[str, JsonValue] = {
            "type": OmpFrameType.PROMPT_RESULT,
            "id": self.prompt_id,
            "agentInvoked": True,
            "status": status,
            "sessionSettled": settled,
        }
        if status == "error":
            result["error"] = {"message": "Initial provider failed", "retryable": False}
        send(result)

    def settle_session(self) -> None:
        self.settled = True
        send({"type": OmpFrameType.SESSION_SETTLED})

    def complete_prompt(self, frame: Frame) -> None:
        self.start_agent_prompt(frame)
        self.complete_agent_turn(COMPLETE)

    def start_agent_prompt(self, frame: Frame) -> None:
        self.respond(frame)
        send({"type": OmpEventType.AGENT_START})

    def complete_agent_turn(self, text: str) -> None:
        self.emit_assistant_turn(text)
        self.send_prompt_result(settled=True)
        self.settle_session()
