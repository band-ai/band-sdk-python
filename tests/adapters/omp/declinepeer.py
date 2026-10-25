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

    def call(
        self, name: BandTool, arguments: dict[str, JsonValue], call_id: str
    ) -> None:
        self.current_tool = name
        send(
            {
                "type": OmpEventType.TOOL_START,
                "toolCallId": call_id,
                "toolName": name,
                "args": arguments,
            }
        )
        send(
            {
                "type": OmpFrameType.HOST_TOOL_CALL,
                "id": call_id,
                "toolCallId": call_id,
                "toolName": name,
                "arguments": arguments,
            }
        )

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
        name = (
            BandTool.GET_PARTICIPANTS
            if self.scenario_name == DeclineScenario.ORDINARY
            else BandTool.NO_REPLY
        )
        self.call(name, {}, DECLINE_CALL)
        if self.scenario_name == DeclineScenario.CONCURRENT:
            self.call(name, {}, SECOND_DECLINE_CALL)

    def finish_aborted(self, status: OmpPromptStatus) -> None:
        self.emit_assistant_turn("", stop=status)
        self.send_prompt_result(settled=True, status=status)
        self.settle_session()

    def handle(self, frame: Frame) -> None:
        with self.ledger.open("a", encoding="utf-8") as handle:
            handle.write(frame.model_dump_json(exclude_unset=True) + "\n")
        match frame.type:
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
                    self.reply()
                else:
                    self.emit_assistant_turn("")
                    self.send_prompt_result(settled=True)
                    self.settle_session()
            case OmpCommandName.ABORT if not self.settled:
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
