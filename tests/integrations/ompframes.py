"""Encode JSONL frames and build messages for local OMP protocol peers."""

from __future__ import annotations

import base64
import json
import sys

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from band.integrations.ompwire import (
    OMP_CHUNK_BYTES,
    OMP_FRAME_BYTES,
    OMP_NATIVE_PROTOCOL_VERSION,
    OmpFrameType,
    OmpPromptStatus,
    OmpUIMethod,
)

PROTOCOL_VERSION = 1
RESUMED_PROVIDER_FAILED = "Resumed provider failed"
UI_APPROVE = "Approve"
UI_DENY = "Deny"


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
    payload = encode_frame(frame)
    if (
        PROTOCOL_VERSION == OMP_NATIVE_PROTOCOL_VERSION
        and len(payload) + 1 > OMP_FRAME_BYTES
    ):
        write_chunk_sequence(payload)
    else:
        write_frame_line(payload)
    sys.stdout.flush()


def encode_frame(frame: dict[str, JsonValue]) -> bytes:
    return json.dumps(frame, separators=(",", ":")).encode("utf-8")


def write_frame_line(payload: bytes) -> None:
    sys.stdout.write(payload.decode("utf-8") + "\n")


def write_chunk_sequence(payload: bytes) -> None:
    count = (len(payload) + OMP_CHUNK_BYTES - 1) // OMP_CHUNK_BYTES
    for index in range(count):
        chunk: dict[str, JsonValue] = {
            "type": OmpFrameType.CHUNK,
            "chunkId": "large-frame",
            "index": index,
            "count": count,
            "byteLength": len(payload),
            "data": base64.b64encode(
                payload[index * OMP_CHUNK_BYTES : (index + 1) * OMP_CHUNK_BYTES]
            ).decode("ascii"),
        }
        write_frame_line(encode_frame(chunk))


def assistant_message(text: str, *, stop: str = "stop") -> dict[str, JsonValue]:
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


def send_host_tool_request(
    request_id: str,
    tool_call_id: str,
    tool_name: str,
    arguments: dict[str, JsonValue],
) -> None:
    send(
        {
            "type": OmpFrameType.HOST_TOOL_CALL,
            "id": request_id,
            "toolCallId": tool_call_id,
            "toolName": tool_name,
            "arguments": arguments,
        }
    )


def send_approval_request(request_id: str) -> None:
    send(
        {
            "type": OmpFrameType.UI_REQUEST,
            "id": request_id,
            "method": OmpUIMethod.SELECT,
            "title": "Allow command?",
            "options": [UI_APPROVE, UI_DENY],
        }
    )


def enable_chunked_frames() -> None:
    global PROTOCOL_VERSION
    PROTOCOL_VERSION = OMP_NATIVE_PROTOCOL_VERSION
