"""Script framing, command-correlation and process failures."""

from __future__ import annotations

import sys
import threading

from pydantic import JsonValue

from band.integrations.ompwire import (
    OMP_FRAME_BYTES,
    OMP_NATIVE_PROTOCOL_VERSION,
    OMP_REASSEMBLED_BYTES,
    OmpCommandName,
    OmpEventType,
    OmpFrameType,
)
from tests.integrations.ompframes import (
    Frame,
    assistant_message,
    enable_chunked_frames,
    send,
)
from tests.integrations.omppeers.base import (
    BACKGROUND_COMPLETE,
    COMPLETE,
    YIELDED,
    RpcPeer,
)
from tests.integrations.omppeers.process import CRASH_EXIT
from tests.integrations.ompscenarios import PeerCommand, PeerScenario

FRAME_PAYLOAD_MARK = "omp-frame-payload-must-not-be-logged"
READY_BURST_FRAME_BYTES = 256


class TransportPeer(RpcPeer):
    SCENARIOS = frozenset(
        {
            PeerScenario.BAD_ACCEPTANCE,
            PeerScenario.BAD_EVENT,
            PeerScenario.BAD_JSON,
            PeerScenario.BAD_NEGOTIATION,
            PeerScenario.STANDARD_TURN,
            PeerScenario.CHUNK_EOF,
            PeerScenario.CHUNK_INTERRUPTED,
            PeerScenario.CHUNK_INVALID_DATA,
            PeerScenario.CHUNK_LENGTH,
            PeerScenario.CHUNK_ORDER,
            PeerScenario.CHUNK_OVERFLOW,
            PeerScenario.DESCENDANTS,
            PeerScenario.EOF,
            PeerScenario.EOF_DESCENDANTS,
            PeerScenario.FRAME_OVERFLOW,
            PeerScenario.LARGE_IMAGE,
            PeerScenario.ADMITTED_PROMPT_ERROR,
            PeerScenario.LOCAL_COMMAND,
            PeerScenario.MISSING_STATE,
            PeerScenario.NEGOTIATION_REJECTED,
            PeerScenario.NO_READY,
            PeerScenario.NO_READY_DESCENDANTS,
            PeerScenario.NO_V2,
            PeerScenario.OVERSIZE_AFTER_READY,
            PeerScenario.READY_LIMIT_BURST,
            PeerScenario.REJECTED_PROMPT,
            PeerScenario.STDIN_BACKPRESSURE,
            PeerScenario.STUBBORN,
            PeerScenario.WRONG_COMMAND,
        }
    )

    def __init__(self, scenario: PeerScenario) -> None:
        super().__init__()
        if scenario not in self.SCENARIOS:
            raise ValueError(f"Unsupported {type(self).__name__} script: {scenario}")
        self.scenario = scenario

    def prompt(self, frame: Frame) -> None:
        self.begin_prompt(frame)
        if self.is_follow_up(frame):
            self.complete_prompt(frame)
            return
        match self.scenario:
            case PeerScenario.REJECTED_PROMPT:
                self.reject_prompt(frame)
            case PeerScenario.ADMITTED_PROMPT_ERROR:
                self.fail_admitted_prompt(frame)
            case PeerScenario.LOCAL_COMMAND:
                self.settled = True
                self.respond(frame, {"agentInvoked": False})
            case PeerScenario.BAD_ACCEPTANCE:
                self.respond(frame, {"agentInvoked": "yes"})
            case PeerScenario.EOF:
                self.start_agent_prompt(frame)
                raise SystemExit(2)
            case PeerScenario.BAD_JSON:
                sys.stdout.write("{" + FRAME_PAYLOAD_MARK + "\n")
                sys.stdout.flush()
            case PeerScenario.BAD_EVENT:
                self.respond(frame)
                send({"type": OmpEventType.MESSAGE_END, "message": {"role": 123}})
            case PeerScenario.WRONG_COMMAND:
                self.send_mismatched_prompt_response(frame)
            case _:
                self.start_agent_prompt(frame)
                self.emit_initial_turn()

    def emit_initial_turn(self) -> None:
        match self.scenario:
            case (
                PeerScenario.CHUNK_OVERFLOW
                | PeerScenario.CHUNK_ORDER
                | PeerScenario.CHUNK_INVALID_DATA
                | PeerScenario.CHUNK_LENGTH
                | PeerScenario.CHUNK_EOF
                | PeerScenario.CHUNK_INTERRUPTED
            ):
                self.send_invalid_chunk_sequence()
            case PeerScenario.FRAME_OVERFLOW:
                send(
                    {
                        "type": OmpFrameType.FRAME_ERROR,
                        "originalType": OmpEventType.MESSAGE_END,
                        "error": "RPC frame exceeded the transport limit",
                    }
                )
            case PeerScenario.LARGE_IMAGE:
                self.send_large_image_turn()
            case PeerScenario.STANDARD_TURN:
                self.send_standard_turn()
            case (
                PeerScenario.STUBBORN
                | PeerScenario.DESCENDANTS
                | PeerScenario.EOF_DESCENDANTS
                | PeerScenario.MISSING_STATE
            ):
                self.emit_assistant_turn(YIELDED, pending=True)
                self.send_prompt_result(settled=False)
            case _:
                raise ValueError(f"No prompt exchange defined for {self.scenario}")

    def state_snapshot(self) -> dict[str, JsonValue]:
        state = super().state_snapshot()
        if self.scenario == PeerScenario.MISSING_STATE:
            state.pop("isSettled")
        return state

    def abort_prompt(self, frame: Frame) -> None:
        if self.scenario != PeerScenario.STUBBORN:
            super().abort_prompt(frame)

    def handle_command(self, frame: Frame) -> None:
        match frame.type:
            case PeerCommand.FIRST | PeerCommand.HOLD:
                self.hold_request(frame)
            case PeerCommand.SECOND:
                self.respond_out_of_order(frame)
            case PeerCommand.FAIL_REQUEST:
                send(
                    {
                        "type": OmpFrameType.RESPONSE,
                        "id": frame.id,
                        "command": frame.type,
                        "success": False,
                        "error": "Command failed",
                        "code": "bad_command",
                    }
                )
            case PeerCommand.EXIT_PROCESS:
                raise SystemExit(CRASH_EXIT)
            case PeerCommand.FILL_STDERR:
                self.fill_stderr(frame)
            case PeerCommand.RELEASE_PROMPT:
                send({"type": OmpEventType.AGENT_START})
                self.emit_assistant_turn(BACKGROUND_COMPLETE)
                self.settle_session()
                self.respond(frame)
            case _:
                super().handle_command(frame)

    def send_invalid_chunk_sequence(self) -> None:
        chunk: dict[str, JsonValue] = {
            "type": OmpFrameType.CHUNK,
            "chunkId": "invalid-frame",
            "index": 0,
            "count": 2,
            "byteLength": OMP_FRAME_BYTES,
            "data": "e30=",
        }
        match self.scenario:
            case PeerScenario.CHUNK_OVERFLOW:
                chunk["byteLength"] = OMP_REASSEMBLED_BYTES + 1
                send(chunk)
            case PeerScenario.CHUNK_ORDER:
                chunk["index"] = 1
                send(chunk)
            case PeerScenario.CHUNK_INVALID_DATA:
                chunk["data"] = "!"
                send(chunk)
            case PeerScenario.CHUNK_LENGTH:
                send(chunk)
                chunk["index"] = 1
                send(chunk)
            case PeerScenario.CHUNK_EOF:
                send(chunk)
                raise SystemExit(2)
            case PeerScenario.CHUNK_INTERRUPTED:
                send(chunk)
                send({"type": "notice", "level": "info", "message": "Interrupted"})
            case _:
                raise ValueError(f"No chunk fault defined for {self.scenario}")

    def reject_prompt(self, frame: Frame) -> None:
        self.settled = True
        send(
            {
                "type": OmpFrameType.RESPONSE,
                "id": frame.id,
                "command": OmpCommandName.PROMPT,
                "success": False,
                "error": "Prompt rejected",
                "code": "rejected",
            }
        )

    def fail_admitted_prompt(self, frame: Frame) -> None:
        self.respond(frame)
        send(
            {
                "type": OmpFrameType.RESPONSE,
                "id": frame.id,
                "command": OmpCommandName.PROMPT,
                "success": False,
                "error": "Admitted prompt failed later",
                "code": "post_admit",
            }
        )

    def send_mismatched_prompt_response(self, frame: Frame) -> None:
        send(
            {
                "type": OmpFrameType.RESPONSE,
                "id": frame.id,
                "command": OmpCommandName.GET_STATE,
                "success": True,
                "data": {},
            }
        )

    def send_large_image_turn(self) -> None:
        message = assistant_message(COMPLETE)
        message["content"] = [
            {
                "type": "image",
                "mimeType": "image/png",
                "data": "YWJj" * 300_000,
            }
        ]
        send(
            {
                "type": OmpEventType.MESSAGE_END,
                "messageId": "large-image",
                "message": message,
            }
        )
        send(
            {"type": OmpEventType.AGENT_END, "messages": [message], "isTerminal": True}
        )
        self.send_prompt_result(settled=True)
        self.settle_session()

    def send_standard_turn(self) -> None:
        send({"type": "queue_update", "steering": [], "followUp": []})
        send({"type": "available_commands_update", "commands": []})
        send({"type": "model_changed"})
        send(
            {
                "type": OmpEventType.TOOL_START,
                "toolCallId": "native-call",
                "toolName": "read",
                "args": {"path": "note.txt"},
            }
        )
        send(
            {
                "type": OmpEventType.TOOL_END,
                "toolCallId": "native-call",
                "toolName": "read",
                "result": {
                    "content": [{"type": "text", "text": "note"}],
                    "details": {"lines": 1},
                },
                "isError": False,
            }
        )
        self.complete_agent_turn(COMPLETE)

    def negotiate_protocol(self, frame: Frame) -> None:
        if self.scenario == PeerScenario.NEGOTIATION_REJECTED:
            send(
                {
                    "type": OmpFrameType.RESPONSE,
                    "id": frame.id,
                    "command": frame.type,
                    "success": False,
                    "error": "Native protocol v2 is not supported",
                }
            )
            return
        self.respond(
            frame,
            {
                "protocolVersion": 1
                if self.scenario == PeerScenario.BAD_NEGOTIATION
                else OMP_NATIVE_PROTOCOL_VERSION
            },
        )
        enable_chunked_frames()

    def hold_request(self, frame: Frame) -> None:
        if self.scenario == PeerScenario.STDIN_BACKPRESSURE:
            self.respond(frame)
            threading.Event().wait()
            return
        self.held = frame

    def respond_out_of_order(self, frame: Frame) -> None:
        self.respond(frame, {"value": "second"})
        if self.held is not None:
            self.respond(self.held, {"value": "first"})
            self.held = None

    def fill_stderr(self, frame: Frame) -> None:
        sys.stderr.write(
            FRAME_PAYLOAD_MARK * (2 * OMP_FRAME_BYTES // len(FRAME_PAYLOAD_MARK))
        )
        sys.stderr.flush()
        self.respond(frame, {"drained": True})
