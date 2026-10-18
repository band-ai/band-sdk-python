"""Local OMP RPC peer with input-driven settlement and cancellation barriers."""

from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import socket
import subprocess
import sys
import threading
from typing import cast

import psutil
from pydantic import BaseModel, ConfigDict, Field, JsonValue

PROTOCOL_VERSION = 1
FRAME_BYTES = 1024 * 1024
CHUNK_BYTES = 256 * 1024


class Frame(BaseModel):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)

    type: str
    id: str | None = None
    message: str | None = None
    result: dict[str, JsonValue] | None = None
    isError: bool | None = None
    value: str | None = None
    confirmed: bool | None = None
    cancelled: bool | None = None


class ProcessReceipt(BaseModel):
    pid: int
    parent_pid: int
    created: float


def start_descendant(scenario: str, port: int, stdio: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        (
            sys.executable,
            "-u",
            __file__,
            "--scenario",
            scenario,
            "--gate-port",
            str(port),
        ),
        # A parent's pending pipe read can block Python's stdin initialization on Windows.
        # Descendants are controlled by the gate socket, not the RPC input pipe.
        stdin=subprocess.DEVNULL,
        stdout=None if stdio == "inherit" else subprocess.DEVNULL,
        stderr=None if stdio == "inherit" else subprocess.DEVNULL,
    )


def hold_process_gate(port: int, *, spawn_child: bool) -> None:
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = start_descendant("gated-child", port, "inherit") if spawn_child else None
    with socket.create_connection(("127.0.0.1", port), timeout=5) as gate:
        gate.settimeout(None)
        receipt = ProcessReceipt(
            pid=os.getpid(),
            parent_pid=os.getppid(),
            created=psutil.Process().create_time(),
        )
        gate.sendall((receipt.model_dump_json() + "\n").encode())
        gate.recv(1)
    if child is not None:
        child.wait(timeout=5)


def send(frame: dict[str, JsonValue]) -> None:
    payload = json.dumps(frame, separators=(",", ":")).encode("utf-8")
    if PROTOCOL_VERSION == 2 and len(payload) + 1 > FRAME_BYTES:
        count = (len(payload) + CHUNK_BYTES - 1) // CHUNK_BYTES
        for index in range(count):
            chunk: dict[str, JsonValue] = {
                "type": "rpc_chunk",
                "chunkId": "large-frame",
                "index": index,
                "count": count,
                "byteLength": len(payload),
                "data": base64.b64encode(
                    payload[index * CHUNK_BYTES : (index + 1) * CHUNK_BYTES]
                ).decode("ascii"),
            }
            sys.stdout.write(json.dumps(chunk, separators=(",", ":")) + "\n")
    else:
        sys.stdout.write(payload.decode("utf-8") + "\n")
    sys.stdout.flush()


def assistant(text: str, *, stop: str = "stop") -> dict[str, JsonValue]:
    message: dict[str, JsonValue] = {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "Considering the result"},
            {"type": "text", "text": text},
        ],
        "api": "test-api",
        "provider": "test-provider",
        "model": "test-model",
        "stopReason": stop,
        "timestamp": 1,
        "usage": {
            "input": 7,
            "output": 3,
            "cacheRead": 2,
            "cacheWrite": 1,
            "totalTokens": 13,
            "cost": {
                "input": 0.1,
                "output": 0.2,
                "cacheRead": 0.01,
                "cacheWrite": 0.02,
                "total": 0.33,
            },
        },
    }
    if stop == "error":
        message["errorMessage"] = "Resumed provider failed"
    return message


class Peer:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.settled = True
        self.prompt_id: str | None = None
        self.prompt_count = 0
        self.frames: list[dict[str, JsonValue]] = []
        self.tool_results: set[str] = set()
        self.ui_answered = False
        self.held: Frame | None = None

    def respond(self, frame: Frame, data: dict[str, JsonValue] | None = None) -> None:
        result: dict[str, JsonValue] = {
            "type": "response",
            "id": frame.id,
            "command": frame.type,
            "success": True,
        }
        if data is not None:
            result["data"] = data
        send(result)

    def end(
        self,
        text: str,
        *,
        stop: str = "stop",
        pending: bool = False,
        compacted: bool = False,
    ) -> None:
        message = assistant(text, stop=stop)
        send({"type": "message_end", "messageId": text, "message": message})
        send(
            {
                "type": "agent_end",
                "messages": [] if compacted else [message],
                "isTerminal": not pending,
                "yielded": True,
                "awaitingAsyncWork": pending,
            }
        )

    def result(self, *, settled: bool, status: str = "completed") -> None:
        result: dict[str, JsonValue] = {
            "type": "prompt_result",
            "id": self.prompt_id,
            "agentInvoked": True,
            "status": status,
            "sessionSettled": settled,
        }
        if status == "error":
            result["error"] = {"message": "Initial provider failed", "retryable": False}
        send(result)

    def settle(self) -> None:
        self.settled = True
        send({"type": "session_settled"})

    def finish(self, frame: Frame) -> None:
        self.respond(frame)
        send({"type": "agent_start"})
        self.end("complete")
        self.result(settled=True)
        self.settle()

    def invalid_chunk(self) -> None:
        chunk: dict[str, JsonValue] = {
            "type": "rpc_chunk",
            "chunkId": "invalid-frame",
            "index": 0,
            "count": 2,
            "byteLength": 1024 * 1024,
            "data": "e30=",
        }
        match self.scenario:
            case "chunk-overflow":
                chunk["byteLength"] = 64 * 1024 * 1024 + 1
                send(chunk)
            case "chunk-order":
                chunk["index"] = 1
                send(chunk)
            case "chunk-invalid-data":
                chunk["data"] = "!"
                send(chunk)
            case "chunk-length":
                send(chunk)
                chunk["index"] = 1
                send(chunk)
            case "chunk-eof":
                send(chunk)
                raise SystemExit(2)
            case _:
                send(chunk)
                send({"type": "notice", "level": "info", "message": "Interrupted"})

    def prompt(self, frame: Frame) -> None:
        self.prompt_id = frame.id
        self.prompt_count += 1
        self.settled = False
        if self.prompt_count > 1 or frame.message == "second":
            self.finish(frame)
            return
        match self.scenario:
            case "rejected":
                self.settled = True
                send(
                    {
                        "type": "response",
                        "id": frame.id,
                        "command": "prompt",
                        "success": False,
                        "error": "Prompt rejected",
                        "code": "rejected",
                    }
                )
                return
            case "local":
                self.settled = True
                self.respond(frame, {"agentInvoked": False})
                return
            case "bad-acceptance":
                self.respond(frame, {"agentInvoked": "yes"})
                return
            case "eof":
                self.respond(frame)
                send({"type": "agent_start"})
                raise SystemExit(2)
            case "bad-json":
                sys.stdout.write("{broken\n")
                sys.stdout.flush()
                return
            case "bad-event":
                self.respond(frame)
                send({"type": "message_end", "message": {"role": 123}})
                return
            case "wrong-command":
                send(
                    {
                        "type": "response",
                        "id": frame.id,
                        "command": "get_state",
                        "success": True,
                        "data": {},
                    }
                )
                return
            case "callbacks":
                self.held = frame
                send({"type": "agent_start"})
                for number in (1, 2):
                    send(
                        {
                            "type": "host_tool_call",
                            "id": f"host-{number}",
                            "toolCallId": f"call-{number}",
                            "toolName": "band_chat",
                            "arguments": {"number": number},
                        }
                    )
                send(
                    {
                        "type": "extension_ui_request",
                        "id": "approval-1",
                        "method": "select",
                        "title": "Allow command?",
                        "options": ["Approve", "Deny"],
                    }
                )
                return
            case "stale":
                send({"type": "session_settled"})
                send(
                    {
                        "type": "prompt_result",
                        "id": "old-prompt",
                        "agentInvoked": True,
                        "status": "completed",
                        "sessionSettled": True,
                    }
                )
            case _:
                pass
        self.respond(frame)
        send({"type": "agent_start"})
        match self.scenario:
            case scenario if scenario.startswith("chunk-"):
                self.invalid_chunk()
            case "frame-overflow":
                send(
                    {
                        "type": "rpc_frame_error",
                        "originalType": "message_end",
                        "error": "RPC frame exceeded the transport limit",
                    }
                )
            case "large-image":
                message = assistant("complete")
                message["content"] = [
                    {
                        "type": "image",
                        "mimeType": "image/png",
                        "data": "YWJj" * 300_000,
                    }
                ]
                send(
                    {
                        "type": "message_end",
                        "messageId": "large-image",
                        "message": message,
                    }
                )
                send({"type": "agent_end", "messages": [message], "isTerminal": True})
                self.result(settled=True)
                self.settle()
            case "basic":
                send({"type": "queue_update", "steering": [], "followUp": []})
                send({"type": "available_commands_update", "commands": []})
                send({"type": "model_changed"})
                send(
                    {
                        "type": "tool_execution_start",
                        "toolCallId": "native-call",
                        "toolName": "read",
                        "args": {"path": "note.txt"},
                    }
                )
                send(
                    {
                        "type": "tool_execution_end",
                        "toolCallId": "native-call",
                        "toolName": "read",
                        "result": {
                            "content": [{"type": "text", "text": "note"}],
                            "details": {"lines": 1},
                        },
                        "isError": False,
                    }
                )
                self.end("complete")
                self.result(settled=True)
                self.settle()
            case "settlement-before-result":
                self.end("complete")
                self.settle()
            case "no-result":
                self.end("complete")
                self.settle()
            case "host-cancel":
                send(
                    {
                        "type": "host_tool_call",
                        "id": "host-cancelled",
                        "toolCallId": "call-cancelled",
                        "toolName": "band_chat",
                        "arguments": {},
                    }
                )
            case "ui-cancel" | "ui-error":
                send(
                    {
                        "type": "extension_ui_request",
                        "id": "ui-cancelled",
                        "method": "select",
                        "title": "Allow command?",
                        "options": ["Approve", "Deny"],
                    }
                )
            case "initial-error" | "initial-aborted":
                stop = "error" if self.scenario == "initial-error" else "aborted"
                self.end("failed", stop=stop)
                self.result(settled=True, status=stop)
                self.settle()
            case _:
                self.end("yielded", pending=True)
                self.result(settled=self.scenario == "false-result-settled")
                if self.scenario == "state-settles":
                    self.settled = True

    def handle(self, frame: Frame) -> None:
        global PROTOCOL_VERSION
        self.frames.append(
            cast(dict[str, JsonValue], frame.model_dump(exclude_unset=True))
        )
        match frame.type:
            case "negotiate_protocol":
                if self.scenario == "negotiation-rejected":
                    send(
                        {
                            "type": "response",
                            "id": frame.id,
                            "command": frame.type,
                            "success": False,
                            "error": "Native protocol v2 is not supported",
                        }
                    )
                    return
                self.respond(
                    frame,
                    {"protocolVersion": 1 if self.scenario == "bad-negotiation" else 2},
                )
                PROTOCOL_VERSION = 2
            case "set_ask_dialog":
                self.respond(frame, {"enabled": True})
            case "prompt":
                self.prompt(frame)
            case "get_state":
                self.respond(
                    frame,
                    {
                        "sessionId": "session-local",
                        "sessionFile": "/local/session.jsonl",
                        "model": {
                            "id": "test-model",
                            "provider": "test-provider",
                            "name": "Test model",
                        },
                        **(
                            {"isSettled": self.settled}
                            if self.scenario != "missing-state"
                            else {}
                        ),
                        "isStreaming": False,
                        "hasPendingAsyncWork": not self.settled,
                        "queuedMessageCount": 0,
                    },
                )
                if self.scenario == "settles-during-state" and not self.settled:
                    self.settle()
            case "release":
                if self.scenario == "settlement-before-result":
                    self.result(settled=False)
                else:
                    send({"type": "agent_start"})
                    stop = {
                        "resume-error": "error",
                        "resume-error-compacted": "error",
                        "resume-aborted": "aborted",
                    }.get(self.scenario, "stop")
                    self.end(
                        "background complete",
                        stop=stop,
                        compacted=self.scenario == "resume-error-compacted",
                    )
                    self.settle()
                self.respond(frame)
            case "cancel-host":
                send(
                    {
                        "type": "host_tool_cancel",
                        "id": "cancel-frame",
                        "targetId": "host-cancelled",
                    }
                )
                self.end("host cancelled")
                self.result(settled=True)
                self.settle()
                self.respond(frame)
            case "cancel-ui":
                send(
                    {
                        "type": "extension_ui_request",
                        "id": "ui-cancel-frame",
                        "method": "cancel",
                        "targetId": "ui-cancelled",
                    }
                )
                self.end("ui cancelled")
                self.result(settled=True)
                self.settle()
                self.respond(frame)
            case "host_tool_result" | "extension_ui_response":
                if frame.type == "host_tool_result" and frame.id is not None:
                    self.tool_results.add(frame.id)
                if frame.type == "extension_ui_response":
                    self.ui_answered = True
                if (
                    self.scenario == "callbacks"
                    and self.tool_results == {"host-1", "host-2"}
                    and self.ui_answered
                    and self.held is not None
                ):
                    self.respond(self.held)
                    self.held = None
                    self.end("callbacks complete")
                    self.result(settled=True)
                    self.settle()
            case "checkpoint":
                self.respond(frame, {"pid": os.getpid(), "frames": self.frames})
            case "abort":
                if self.scenario != "stubborn":
                    self.respond(frame)
                    if self.prompt_id is not None:
                        self.end("aborted", stop="aborted")
                        self.settle()
            case "first" | "hold":
                self.held = frame
            case "second":
                self.respond(frame, {"value": "second"})
                if self.held is not None:
                    self.respond(self.held, {"value": "first"})
                    self.held = None
            case "error":
                send(
                    {
                        "type": "response",
                        "id": frame.id,
                        "command": frame.type,
                        "success": False,
                        "error": "Command failed",
                        "code": "bad_command",
                    }
                )
            case "crash":
                raise SystemExit(3)
            case "stderr":
                sys.stderr.write("x" * (2 * 1024 * 1024))
                sys.stderr.flush()
                self.respond(frame, {"drained": True})
            case _:
                self.respond(frame)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--gate-port", type=int)
    parser.add_argument(
        "--child-stdio", choices=("inherit", "discard"), default="inherit"
    )
    args = parser.parse_args()
    if args.scenario in {"gated-parent", "gated-child"}:
        if args.gate_port is None:
            raise ValueError("A process gate port is required")
        hold_process_gate(args.gate_port, spawn_child=args.scenario == "gated-parent")
        return
    if args.scenario in {"descendants", "no-ready-descendants"}:
        if args.gate_port is None:
            raise ValueError("A process gate port is required")
        # The gate, not the peer's stdio EOF, owns these children's lifetime.
        start_descendant("gated-parent", args.gate_port, args.child_stdio)
    if args.scenario == "stubborn" and hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    peer = Peer(args.scenario)
    if args.scenario not in {"no-ready", "no-ready-descendants"}:
        send(
            {
                "type": "ready",
                "protocolVersion": 1,
                "supportedProtocolVersions": [1]
                if args.scenario == "no-v2"
                else [1, 2],
                "maxFrameBytes": 1024 * 1024,
                "maxReassembledFrameBytes": 64 * 1024 * 1024,
            }
        )
    for line in sys.stdin:
        peer.handle(Frame.model_validate_json(line, strict=True))
    if args.scenario == "stubborn":
        threading.Event().wait()


if __name__ == "__main__":
    main()
