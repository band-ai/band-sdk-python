"""Local OMP RPC peer with input-driven settlement and cancellation barriers."""

from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import subprocess
import sys
import threading
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from band.integrations.ompwire import (
    OMP_CHUNK_BYTES,
    OMP_FRAME_BYTES,
    OMP_NATIVE_PROTOCOL_VERSION,
    OMP_REASSEMBLED_BYTES,
    OmpCommandName,
    OmpPromptStatus,
)
from tests.integrations.ompgate import GateRole, start_descendant

PROTOCOL_VERSION = 1
COMPLETE = "complete"
YIELDED = "yielded"
BACKGROUND_COMPLETE = "background complete"
RESUMED_PROVIDER_FAILED = "Resumed provider failed"
FOLLOW_UP_PROMPT = "second"
CRASH_EXIT = 3
FRAME_PAYLOAD_MARK = "omp-frame-payload-must-not-be-logged"
UI_APPROVE = "Approve"
UI_DENY = "Deny"
HOST_CANCELLED_ID = "host-cancelled"
UI_CANCELLED_ID = "ui-cancelled"
OVERSIZE_AFTER_READY = "oversize-after-ready"
READY_LIMIT_BYTES = 1024
LATE_PROMPT_ERROR = "late-prompt-error"
ADMITTED_PROMPT_ERROR = "late-error"
SETTLED_CALLBACK_SCENARIO = "settled-during-callback"
SETTLED_CALLBACK_ID = "host-held"
LATE_CALLBACK_ID = "host-late"
STDIN_BACKPRESSURE = "stdin-backpressure"
LIFECYCLE_SCENARIO = "lifecycle"


def burst_message_id(number: int) -> str:
    return f"burst-{number}"


class Frame(BaseModel):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)

    type: str
    id: str | None = None
    message: str | None = None
    failed_command: str | None = None
    result: dict[str, JsonValue] | None = None
    isError: bool | None = None
    value: str | None = None
    confirmed: bool | None = None
    cancelled: bool | None = None
    count: int = 0


def send(frame: dict[str, JsonValue]) -> None:
    payload = json.dumps(frame, separators=(",", ":")).encode("utf-8")
    if (
        PROTOCOL_VERSION == OMP_NATIVE_PROTOCOL_VERSION
        and len(payload) + 1 > OMP_FRAME_BYTES
    ):
        count = (len(payload) + OMP_CHUNK_BYTES - 1) // OMP_CHUNK_BYTES
        for index in range(count):
            chunk: dict[str, JsonValue] = {
                "type": "rpc_chunk",
                "chunkId": "large-frame",
                "index": index,
                "count": count,
                "byteLength": len(payload),
                "data": base64.b64encode(
                    payload[index * OMP_CHUNK_BYTES : (index + 1) * OMP_CHUNK_BYTES]
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
    if stop == OmpPromptStatus.ERROR:
        message["errorMessage"] = RESUMED_PROVIDER_FAILED
    return message


class Peer:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.settled = True
        self.state_requested = False
        self.prompt_id: str | None = None
        self.prompt_count = 0
        self.frames: list[dict[str, JsonValue]] = []
        self.tool_results: set[str] = set()
        self.ui_answered = False
        self.held: Frame | None = None
        self.async_hint: bool | None = None
        self.state_request_count = 0

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

    def checkpoint(self, frame: Frame) -> None:
        if (
            self.scenario == SETTLED_CALLBACK_SCENARIO
            and self.prompt_id is not None
            and not self.state_requested
        ):
            self.held = frame
            return
        self.respond(frame, {"pid": os.getpid(), "frames": self.frames})

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
        self.end(COMPLETE)
        self.result(settled=True)
        self.settle()

    @staticmethod
    def reused_callback() -> None:
        send(
            {
                "type": "host_tool_call",
                "id": "host-dup",
                "toolCallId": "call-dup",
                "toolName": "band_chat",
                "arguments": {},
            }
        )

    def invalid_chunk(self) -> None:
        chunk: dict[str, JsonValue] = {
            "type": "rpc_chunk",
            "chunkId": "invalid-frame",
            "index": 0,
            "count": 2,
            "byteLength": OMP_FRAME_BYTES,
            "data": "e30=",
        }
        match self.scenario:
            case "chunk-overflow":
                chunk["byteLength"] = OMP_REASSEMBLED_BYTES + 1
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
        self.state_requested = False
        if (
            self.prompt_count > 1 and self.scenario != LIFECYCLE_SCENARIO
        ) or frame.message == FOLLOW_UP_PROMPT:
            if self.scenario == "late-stale-result":
                send(
                    {
                        "type": "prompt_result",
                        "id": "old-prompt",
                        "agentInvoked": True,
                        "status": "completed",
                        "sessionSettled": True,
                    }
                )
            self.finish(frame)
            return
        match self.scenario:
            case "rejected":
                self.settled = True
                send(
                    {
                        "type": "response",
                        "id": frame.id,
                        "command": OmpCommandName.PROMPT,
                        "success": False,
                        "error": "Prompt rejected",
                        "code": "rejected",
                    }
                )
                return
            case scenario if scenario == ADMITTED_PROMPT_ERROR:
                self.respond(frame)
                send(
                    {
                        "type": "response",
                        "id": frame.id,
                        "command": OmpCommandName.PROMPT,
                        "success": False,
                        "error": "Admitted prompt failed later",
                        "code": "post_admit",
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
                sys.stdout.write("{" + FRAME_PAYLOAD_MARK + "\n")
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
                        "command": OmpCommandName.GET_STATE,
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
                        "options": [UI_APPROVE, UI_DENY],
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
            case "stale-idle-result":
                # A fully answered, fully settled turn whose prompt_result
                # carries a stale id: completion evidence never arrives while
                # the native state positively reports idle.
                self.respond(frame)
                send({"type": "agent_start"})
                self.end("complete")
                send(
                    {
                        "type": "prompt_result",
                        "id": "old-prompt",
                        "agentInvoked": True,
                        "status": "completed",
                        "sessionSettled": True,
                    }
                )
                self.settle()
                return
            case "lost-settlement":
                # The correlated result arrives; the session_settled frame is
                # lost and the snapshot reports every idle flag while
                # isSettled stays false.
                self.respond(frame)
                send({"type": "agent_start"})
                self.end("complete")
                self.async_hint = False
                self.result(settled=False)
                return
            case "silent-settled-continuation":
                # Settlement is observed, then a yielded continuation claims
                # background ownership; its completion settles without ever
                # emitting another session_settled frame (it goes idle on
                # release).
                self.respond(frame)
                send({"type": "agent_start"})
                self.end("first")
                self.settle()
                self.settled = False  # the continuation makes the session busy
                send({"type": "agent_start"})
                self.end("continuation", pending=True)
                self.result(settled=False)
                return
            case "drain-stubborn":
                # A healthy answered turn whose host tool callback ignores
                # cancellation: settlement completes, the callback never does.
                self.respond(frame)
                send({"type": "agent_start"})
                send(
                    {
                        "type": "host_tool_call",
                        "id": "host-stubborn",
                        "toolCallId": "call-stubborn",
                        "toolName": "band_chat",
                        "arguments": {},
                    }
                )
                self.end("complete")
                self.result(settled=True)
                self.settle()
                return
            case "drain-blocked-event":
                # Result and settlement complete while one event callback stays
                # undelivered, so the event drain never finishes.
                self.respond(frame)
                send({"type": "agent_start"})
                self.end("complete")
                self.result(settled=True)
                self.settle()
                send(
                    {
                        "type": "message_update",
                        "messageId": "blocked",
                        "message": assistant(""),
                    }
                )
                return
            case "refresh-state-silent":
                # A fully healthy completion; the loss happens later, on the
                # adapter-phase post-prompt refresh (state request #3).
                self.respond(frame)
                send({"type": "agent_start"})
                self.end("complete")
                self.result(settled=True)
                self.settle()
                return
            case "get-state-silent":
                # The correlated result arrives and the settlement snapshot
                # response is lost: no completion path can observe state.
                self.respond(frame)
                send({"type": "agent_start"})
                self.end("complete")
                self.result(settled=False)
                return
            case _:
                pass
        self.respond(frame)
        send({"type": "agent_start"})
        match self.scenario:
            case scenario if scenario.startswith("chunk-"):
                self.invalid_chunk()
            case "event-backpressure":
                return
            case "ui-notify":
                send(
                    {
                        "type": "extension_ui_request",
                        "id": "ui-notify",
                        "method": "notify",
                        "message": "status",
                    }
                )
                self.end(COMPLETE)
                self.result(settled=True)
                self.settle()
            case "reused-callback":
                self.reused_callback()
            case "frame-overflow":
                send(
                    {
                        "type": "rpc_frame_error",
                        "originalType": "message_end",
                        "error": "RPC frame exceeded the transport limit",
                    }
                )
            case "late-stale-result":
                self.end("complete")
                self.result(settled=True)
                self.settle()
                return
            case "large-image":
                message = assistant(COMPLETE)
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
                self.end(COMPLETE)
                self.result(settled=True)
                self.settle()
            case "settlement-before-result":
                self.end(COMPLETE)
                self.settle()
            case "no-result":
                self.end(COMPLETE)
                self.settle()
            case scenario if scenario == SETTLED_CALLBACK_SCENARIO:
                send(
                    {
                        "type": "host_tool_call",
                        "id": SETTLED_CALLBACK_ID,
                        "toolCallId": "call-held",
                        "toolName": "band_chat",
                        "arguments": {},
                    }
                )
                self.end(COMPLETE)
                self.result(settled=True)
                self.settle()
            case "host-cancel":
                send(
                    {
                        "type": "host_tool_call",
                        "id": HOST_CANCELLED_ID,
                        "toolCallId": "call-cancelled",
                        "toolName": "band_chat",
                        "arguments": {},
                    }
                )
            case "ui-cancel" | "ui-error":
                send(
                    {
                        "type": "extension_ui_request",
                        "id": UI_CANCELLED_ID,
                        "method": "select",
                        "title": "Allow command?",
                        "options": [UI_APPROVE, UI_DENY],
                    }
                )
            case "initial-error" | "initial-aborted":
                stop = (
                    OmpPromptStatus.ERROR
                    if self.scenario == "initial-error"
                    else OmpPromptStatus.ABORTED
                )
                self.end("failed", stop=stop)
                self.result(settled=True, status=stop)
                self.settle()
            case _:
                self.end(YIELDED, pending=True)
                self.result(settled=self.scenario == "false-result-settled")
                if self.scenario == "state-settles":
                    self.settled = True

    def handle(self, frame: Frame) -> None:
        global PROTOCOL_VERSION
        self.frames.append(
            cast(dict[str, JsonValue], frame.model_dump(exclude_unset=True))
        )
        match frame.type:
            case OmpCommandName.NEGOTIATE_PROTOCOL:
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
                    {
                        "protocolVersion": 1
                        if self.scenario == "bad-negotiation"
                        else OMP_NATIVE_PROTOCOL_VERSION
                    },
                )
                PROTOCOL_VERSION = OMP_NATIVE_PROTOCOL_VERSION
            case OmpCommandName.SET_ASK_DIALOG:
                self.respond(frame, {"enabled": True})
            case OmpCommandName.PROMPT:
                self.prompt(frame)
            case OmpCommandName.GET_STATE:
                self.state_requested = True
                self.state_request_count += 1
                if self.scenario == "get-state-silent":
                    return  # every snapshot response is lost
                if (
                    self.scenario == "refresh-state-silent"
                    and self.state_request_count > 2
                ):
                    return  # the post-completion refresh response is lost
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
                        "hasPendingAsyncWork": (
                            not self.settled
                            if self.async_hint is None
                            else self.async_hint
                        ),
                        "queuedMessageCount": 0,
                    },
                )
                if (
                    self.scenario == SETTLED_CALLBACK_SCENARIO
                    and self.held is not None
                    and self.held.type == "checkpoint"
                ):
                    self.checkpoint(self.held)
                    self.held = None
                if self.scenario == "settles-during-state" and not self.settled:
                    self.settle()
                if (
                    self.scenario == "event-backpressure"
                    and self.held is not None
                    and self.held.type == "event-result"
                ):
                    self.respond(self.held)
                    self.held = None
            case "event-burst":
                for number in range(frame.count):
                    send(
                        {
                            "type": "message_update",
                            "messageId": burst_message_id(number),
                            "message": {"role": "assistant", "content": str(number)},
                        }
                    )
                self.respond(frame)
            case "event-result":
                self.held = frame
                self.end(COMPLETE, compacted=True)
                self.result(settled=True)
                self.settle()
            case "event-ui":
                self.held = frame
                send(
                    {
                        "type": "extension_ui_request",
                        "id": "backlog-approval",
                        "method": "select",
                        "title": "Allow command?",
                        "options": [UI_APPROVE, UI_DENY],
                    }
                )
            case "repeat-callback":
                self.reused_callback()
                self.respond(frame)
            case "ask-ui":
                self.held = frame
                send(
                    {
                        "type": "extension_ui_request",
                        "id": "structured-question",
                        "method": "ask",
                        "questions": [
                            {
                                "id": "database-choice",
                                "question": "Which database?",
                                "options": [{"label": "SQLite"}],
                            }
                        ],
                    }
                )
            case "release":
                if self.scenario == SETTLED_CALLBACK_SCENARIO:
                    send(
                        {
                            "type": "host_tool_call",
                            "id": LATE_CALLBACK_ID,
                            "toolCallId": f"call-{LATE_CALLBACK_ID}",
                            "toolName": "band_chat",
                            "arguments": {},
                        }
                    )
                    self.respond(frame)
                    return
                if self.scenario == LATE_PROMPT_ERROR:
                    send(
                        {
                            "type": "response",
                            "id": self.prompt_id,
                            "command": frame.failed_command or OmpCommandName.PROMPT,
                            "success": False,
                            "error": "Prompt command failed after admission",
                        }
                    )
                    self.respond(frame)
                    return
                if self.scenario == "settlement-before-result":
                    self.result(settled=False)
                elif self.scenario == "silent-settled-continuation":
                    # The background work completes and the session goes idle,
                    # but its session_settled frame is lost.
                    send({"type": "agent_start"})
                    self.end("background complete", pending=False)
                    self.settled = True
                else:
                    send({"type": "agent_start"})
                    stop = {
                        "resume-error": OmpPromptStatus.ERROR,
                        "resume-error-compacted": OmpPromptStatus.ERROR,
                        "resume-aborted": OmpPromptStatus.ABORTED,
                    }.get(self.scenario, "stop")
                    self.end(
                        BACKGROUND_COMPLETE,
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
                        "targetId": HOST_CANCELLED_ID,
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
                        "targetId": UI_CANCELLED_ID,
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
                    self.scenario == "event-backpressure"
                    and frame.type == "extension_ui_response"
                    and frame.id == "backlog-approval"
                    and self.held is not None
                    and self.held.type == "event-ui"
                ):
                    self.respond(self.held, {"value": frame.value})
                    self.held = None
                if (
                    frame.type == "extension_ui_response"
                    and frame.id == "structured-question"
                    and self.held is not None
                    and self.held.type == "ask-ui"
                ):
                    self.respond(
                        self.held,
                        {
                            "answer": cast(
                                dict[str, JsonValue],
                                frame.model_dump(exclude_unset=True),
                            )
                        },
                    )
                    self.held = None
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
            case "orphan-tool":
                send(
                    {
                        "type": "host_tool_call",
                        "id": "host-orphan",
                        "toolCallId": "call-orphan",
                        "toolName": "band_chat",
                        "arguments": {},
                    }
                )
            case "orphan-ui":
                send(
                    {
                        "type": "extension_ui_request",
                        "id": "ui-orphan",
                        "method": "select",
                        "title": "Allow command?",
                        "options": [UI_APPROVE, UI_DENY],
                    }
                )
                self.respond(frame)
            case "orphan-ui-cancel":
                send(
                    {
                        "type": "extension_ui_request",
                        "id": "ui-cancel-orphan",
                        "method": "cancel",
                    }
                )
            case "checkpoint":
                self.checkpoint(frame)
            case OmpCommandName.ABORT:
                if self.scenario != "stubborn":
                    self.respond(frame)
                    if self.prompt_id is not None:
                        self.end(OmpPromptStatus.ABORTED, stop=OmpPromptStatus.ABORTED)
                        self.settle()
            case "first" | "hold":
                if self.scenario == STDIN_BACKPRESSURE:
                    self.respond(frame)
                    threading.Event().wait()
                    return
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
                raise SystemExit(CRASH_EXIT)
            case "stderr":
                sys.stderr.write(
                    FRAME_PAYLOAD_MARK
                    * (2 * OMP_FRAME_BYTES // len(FRAME_PAYLOAD_MARK))
                )
                sys.stderr.flush()
                self.respond(frame, {"drained": True})
            case _:
                self.respond(frame)


def announce_ready(scenario: str) -> None:
    ready: dict[str, JsonValue] = {
        "type": "ready",
        "protocolVersion": 1,
        "supportedProtocolVersions": [1]
        if scenario == "no-v2"
        else [1, OMP_NATIVE_PROTOCOL_VERSION],
        "maxFrameBytes": OMP_FRAME_BYTES,
        "maxReassembledFrameBytes": OMP_REASSEMBLED_BYTES,
    }
    if scenario == OVERSIZE_AFTER_READY:
        ready["maxFrameBytes"] = READY_LIMIT_BYTES
        frames = (
            ready,
            {"type": "session_settled", "padding": "x" * READY_LIMIT_BYTES},
        )
        # One write keeps the oversized frame buffered behind its ready limits.
        sys.stdout.write(
            "\n".join(json.dumps(frame, separators=(",", ":")) for frame in frames)
            + "\n"
        )
        sys.stdout.flush()
        return
    send(ready)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--gate-port", type=int)
    parser.add_argument("--detached-children", action="store_true")
    parser.add_argument("--spawn-on-eof", action="store_true")
    parser.add_argument(
        "--child-stdio", choices=("inherit", "discard"), default="inherit"
    )
    # Adapter launches also append native CLI flags this peer does not consume.
    args, _unknown = parser.parse_known_args()
    managed_child: subprocess.Popen[bytes] | None = None
    if args.gate_port is not None and args.scenario in {
        "descendants",
        "no-ready-descendants",
        LIFECYCLE_SCENARIO,
        "eof-descendants",
    }:
        # The gate, not the peer's stdio EOF, owns these children's lifetime.
        managed_child = start_descendant(
            GateRole.PARENT,
            args.gate_port,
            args.child_stdio,
            detached=args.detached_children,
            spawn_on_eof=args.scenario == "eof-descendants",
        )
    if args.scenario == "stubborn" and hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    peer = Peer(args.scenario)
    if args.scenario not in {"no-ready", "no-ready-descendants"}:
        announce_ready(args.scenario)
    for line in sys.stdin:
        peer.handle(Frame.model_validate_json(line, strict=True))
    if managed_child is not None and managed_child.stdin is not None:
        managed_child.stdin.close()
    if args.scenario == "stubborn":
        threading.Event().wait()


if __name__ == "__main__":
    main()
