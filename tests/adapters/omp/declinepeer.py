"""Drive terminal declines and their continuations over native RPC."""

from __future__ import annotations

import json
import sys
from enum import StrEnum
from pathlib import Path

from pydantic import JsonValue

from band.integrations.ompprocess import OMP_MCP_RECEIPT, omp_mcp_ready_command
from band.integrations.ompwire import (
    OMP_FRAME_BYTES,
    OMP_NATIVE_PROTOCOL_VERSION,
    OMP_REASSEMBLED_BYTES,
    OmpCommandName,
    OmpContentKind,
    OmpEventType,
    OmpFrameType,
    OmpPromptStatus,
)
from band.runtime.tools import BandTool
from tests.integrations.ompframes import Frame, send
from tests.integrations.omppeers.base import FOLLOW_UP_PROMPT, RpcPeer

DECLINE_CALL = "decline"
SECOND_DECLINE_CALL = "decline-concurrent"
STOP_CHECKPOINT = "stop-checkpoint"
ADMIT_SECOND_DECLINE = "admit-second-decline"
DECLINE_TRANSCRIPT = "frames.jsonl"
REPLY_CALL = "reply"
REPLY_TEXT = "Acknowledged."


class DeclineScenario(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    ORDINARY = "ordinary"
    ABORTED = "aborted"
    PROVIDER_ERROR = "provider-error"
    CONCURRENT = "concurrent"
    HELD_STOP = "held-stop"
    BACKGROUND_PENDING = "background-pending"
    BACKGROUND_QUEUED = "background-queued"
    BACKGROUND_COMPLETING = "background-completing"
    MIXED_BATCH = "mixed-batch"
    MISSING_BATCH = "missing-batch"
    STRING_CONTENT = "string-content"
    MISMATCHED_ID = "mismatched-id"
    POST_CHECK_PENDING = "post-check-pending"
    CANCEL_SNAPSHOT = "cancel-snapshot"
    CANCEL_AFTER_ABORT = "cancel-after-abort"


class DeclinePeer(RpcPeer):
    def __init__(self) -> None:
        super().__init__()
        self.scenario_name = DeclineScenario(sys.argv[1])
        self.ledger = Path(sys.argv[2])
        self.plugin = (
            Path(sys.argv[sys.argv.index("--plugin-dir") + 1])
            if "--plugin-dir" in sys.argv
            else None
        )
        self.current_tool = BandTool.NO_REPLY
        self.stop_pending = False
        self.stop_checkpoint: Frame | None = None
        self.background_pending = False
        self.background_queued = False
        self.cancel_snapshot_pending = False
        self.post_check_pending = False
        self.stop_check_seen = False

    def call(
        self, name: BandTool, arguments: dict[str, JsonValue], call_id: str
    ) -> None:
        self.current_tool = name
        if not (
            self.scenario_name == DeclineScenario.MISSING_BATCH
            and call_id == DECLINE_CALL
        ):
            if (
                self.scenario_name == DeclineScenario.STRING_CONTENT
                and call_id == DECLINE_CALL
            ):
                message_content: JsonValue = f"{name}({arguments})"
            else:
                tool_call_id = (
                    "other-id"
                    if (
                        self.scenario_name == DeclineScenario.MISMATCHED_ID
                        and call_id == DECLINE_CALL
                    )
                    else call_id
                )
                content: list[JsonValue] = [
                    {
                        "type": OmpContentKind.TOOL_CALL,
                        "id": tool_call_id,
                        "name": name,
                        "arguments": arguments,
                    }
                ]
                if (
                    self.scenario_name == DeclineScenario.MIXED_BATCH
                    and call_id == DECLINE_CALL
                ):
                    content.append(
                        {
                            "type": OmpContentKind.TOOL_CALL,
                            "id": "background",
                            "name": "bash",
                            "arguments": {},
                        }
                    )
                message_content = content
            send(
                {
                    "type": OmpEventType.MESSAGE_END,
                    "message": {"role": "assistant", "content": message_content},
                }
            )
        send(
            {
                "type": OmpEventType.TOOL_START,
                "toolCallId": call_id,
                "toolName": name,
                "args": arguments,
            }
        )
        request: dict[str, JsonValue] = {
            "type": OmpFrameType.HOST_TOOL_CALL,
            "id": call_id,
            "toolCallId": call_id,
            "toolName": name,
            "arguments": arguments,
        }
        with self.ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(request) + "\n")
        send(request)

    def reply(self) -> None:
        self.call(
            BandTool.SEND_MESSAGE,
            {"content": REPLY_TEXT, "mentions": ["@alice"]},
            REPLY_CALL,
        )

    def prompt(self, frame: Frame) -> None:
        self.prompt_id = frame.id
        self.settled = False
        if frame.message == FOLLOW_UP_PROMPT:
            self.complete_prompt(frame)
            return
        self.respond(frame)
        if self.plugin is not None and frame.message == (
            f"/{omp_mcp_ready_command(str(self.plugin))}"
        ):
            (self.plugin / OMP_MCP_RECEIPT).write_text(
                json.dumps({"systemPromptReady": True, "servers": []})
            )
            self.send_prompt_result(settled=True)
            self.settle_session()
            return
        self.prompt_count += 1
        send({"type": OmpEventType.AGENT_START})
        if self.prompt_count > 1:
            self.reply()
            return
        if self.scenario_name == DeclineScenario.ABORTED:
            self.finish_aborted(OmpPromptStatus.ABORTED)
            return
        self.background_pending = self.scenario_name in {
            DeclineScenario.BACKGROUND_PENDING,
            DeclineScenario.BACKGROUND_COMPLETING,
        }
        self.background_queued = self.scenario_name == DeclineScenario.BACKGROUND_QUEUED
        self.cancel_snapshot_pending = (
            self.scenario_name == DeclineScenario.CANCEL_SNAPSHOT
        )
        self.post_check_pending = False
        self.stop_check_seen = False
        name = (
            BandTool.GET_PARTICIPANTS
            if self.scenario_name == DeclineScenario.ORDINARY
            else BandTool.NO_REPLY
        )
        self.call(name, {}, DECLINE_CALL)

    def state_snapshot(self) -> dict[str, JsonValue]:
        state = super().state_snapshot()
        state["isStreaming"] = not self.settled
        state["hasPendingAsyncWork"] = (
            self.background_pending or self.post_check_pending
        )
        state["queuedMessageCount"] = int(self.background_queued)
        return state

    def finish_aborted(self, status: OmpPromptStatus) -> None:
        self.background_pending = False
        self.background_queued = False
        self.emit_assistant_turn("", stop=status)
        self.send_prompt_result(settled=True, status=status)
        self.settle_session()

    def handle(self, frame: Frame) -> None:
        with self.ledger.open("a", encoding="utf-8") as handle:
            handle.write(frame.model_dump_json(exclude_unset=True) + "\n")
        match frame.type:
            case command if command == ADMIT_SECOND_DECLINE:
                self.call(BandTool.NO_REPLY, {}, SECOND_DECLINE_CALL)
                self.respond(frame)
            case OmpCommandName.GET_STATE:
                if self.scenario_name == DeclineScenario.POST_CHECK_PENDING:
                    self.respond_with_state(frame)
                    if not self.stop_check_seen:
                        self.stop_check_seen = True
                        self.post_check_pending = True
                    return
                if self.cancel_snapshot_pending:
                    self.cancel_snapshot_pending = False
                    frames = [
                        {
                            "type": OmpFrameType.RESPONSE,
                            "id": frame.id,
                            "command": frame.type,
                            "success": True,
                            "data": self.state_snapshot(),
                        },
                        {
                            "type": OmpFrameType.HOST_TOOL_CANCEL,
                            "id": "cancel-decline",
                            "targetId": DECLINE_CALL,
                        },
                    ]
                    sys.stdout.write(
                        "".join(json.dumps(item) + "\n" for item in frames)
                    )
                    sys.stdout.flush()
                    self.send_prompt_result(settled=True)
                    self.settle_session()
                    return
                self.respond_with_state(frame)
                if self.scenario_name == DeclineScenario.BACKGROUND_COMPLETING:
                    self.background_pending = False
            case OmpCommandName.GET_AVAILABLE_COMMANDS:
                self.respond(
                    frame,
                    {"commands": [{"name": omp_mcp_ready_command(str(self.plugin))}]},
                )
            case OmpFrameType.HOST_TOOL_RESULT:
                send(
                    {
                        "type": OmpEventType.TOOL_END,
                        "toolCallId": frame.id,
                        "toolName": self.current_tool,
                        "isError": bool(frame.isError),
                        "result": frame.result,
                    }
                )
                if frame.id in {DECLINE_CALL, SECOND_DECLINE_CALL}:
                    self.background_pending = False
                    self.background_queued = False
                    self.post_check_pending = False
                    self.reply()
                else:
                    self.emit_assistant_turn("")
                    self.send_prompt_result(settled=True)
                    self.settle_session()
            case OmpCommandName.ABORT if not self.settled:
                if self.scenario_name == DeclineScenario.CANCEL_AFTER_ABORT:
                    # Cancel after the abort write but before the abort ack so
                    # stop_task cannot commit DECLINE without a finished stop.
                    send(
                        {
                            "type": OmpFrameType.HOST_TOOL_CANCEL,
                            "id": "cancel-after-abort",
                            "targetId": DECLINE_CALL,
                        }
                    )
                    self.finish_aborted(OmpPromptStatus.ABORTED)
                    return
                status = (
                    OmpPromptStatus.ERROR
                    if self.scenario_name == DeclineScenario.PROVIDER_ERROR
                    else OmpPromptStatus.ABORTED
                )
                self.finish_aborted(status)
                if self.scenario_name == DeclineScenario.HELD_STOP:
                    self.stop_pending = True
                    if self.stop_checkpoint is not None:
                        self.respond(self.stop_checkpoint)
                    return
                self.respond(frame)
            case command if command == STOP_CHECKPOINT:
                if self.stop_pending:
                    self.respond(frame)
                else:
                    self.stop_checkpoint = frame
            case _:
                super().handle(frame)


def main() -> None:
    send(
        {
            "type": OmpFrameType.READY,
            "protocolVersion": 1,
            "supportedProtocolVersions": [1, OMP_NATIVE_PROTOCOL_VERSION],
            "maxFrameBytes": OMP_FRAME_BYTES,
            "maxReassembledFrameBytes": OMP_REASSEMBLED_BYTES,
        }
    )
    peer = DeclinePeer()
    for line in sys.stdin:
        peer.handle(Frame.model_validate_json(line, strict=True))


if __name__ == "__main__":
    main()
