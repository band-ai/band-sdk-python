"""Typed projections of OMP's native JSONL RPC frames."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    model_validator,
)

OMP_FRAME_BYTES = 1024 * 1024
OMP_REASSEMBLED_BYTES = 64 * 1024 * 1024
OMP_CHUNK_BYTES = 256 * 1024


class OmpFrameType(StrEnum):
    READY = "ready"
    RESPONSE = "response"
    PROMPT_RESULT = "prompt_result"
    SESSION_SETTLED = "session_settled"
    HOST_TOOL_CALL = "host_tool_call"
    HOST_TOOL_CANCEL = "host_tool_cancel"
    UI_REQUEST = "extension_ui_request"
    CHUNK = "rpc_chunk"
    FRAME_ERROR = "rpc_frame_error"


class OmpEventType(StrEnum):
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    MESSAGE_START = "message_start"
    MESSAGE_UPDATE = "message_update"
    MESSAGE_END = "message_end"
    TOOL_START = "tool_execution_start"
    TOOL_UPDATE = "tool_execution_update"
    TOOL_STREAM = "tool_stream_update"
    TOOL_END = "tool_execution_end"


class OmpWireModel(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="ignore", strict=True, allow_inf_nan=False
    )


class OmpContent(OmpWireModel):
    """Transcript content, including text, thoughts, images, and tool calls."""

    type: str
    text: str | None = None
    thinking: str | None = None
    data: str | None = None
    mimeType: str | None = None
    id: str | None = None
    name: str | None = None
    arguments: dict[str, JsonValue] | None = None
    intent: str | None = None


class OmpUsageCost(OmpWireModel):
    input: float = 0
    output: float = 0
    cacheRead: float = 0
    cacheWrite: float = 0
    total: float = 0


class OmpUsage(OmpWireModel):
    input: int = 0
    output: int = 0
    cacheRead: int = 0
    cacheWrite: int = 0
    totalTokens: int = 0
    contextTokens: int | None = None
    reasoningTokens: int | None = None
    cost: OmpUsageCost | None = None


class OmpMessage(OmpWireModel):
    role: str
    content: str | list[OmpContent] = Field(default_factory=list)
    stopReason: str | None = None
    errorMessage: str | None = None
    usage: OmpUsage | None = None
    model: str | None = None
    provider: str | None = None
    api: str | None = None
    timestamp: float | None = None
    toolCallId: str | None = None
    toolName: str | None = None
    isError: bool | None = None


class OmpToolResult(OmpWireModel):
    content: list[OmpContent]
    details: JsonValue = None
    is_error: bool = Field(
        default=False, validation_alias=AliasChoices("is_error", "isError")
    )

    @model_validator(mode="after")
    def validate_content(self) -> OmpToolResult:
        for content in self.content:
            match content.type:
                case "text" if content.text is not None:
                    continue
                case "image" if (
                    content.data is not None and content.mimeType is not None
                ):
                    continue
                case _:
                    raise ValueError("OMP tool content must be text or an inline image")
        return self


class OmpAssistantEvent(OmpWireModel):
    type: str
    delta: str | None = None
    contentIndex: int | None = None
    content: JsonValue = None


class OmpEvent(OmpWireModel):
    type: str
    message: OmpMessage | None = None
    messages: list[OmpMessage] = Field(default_factory=list)
    messageId: str | None = None
    assistantMessageEvent: OmpAssistantEvent | None = None
    toolCallId: str | None = None
    toolName: str | None = None
    args: JsonValue = None
    result: OmpToolResult | None = None
    partialResult: JsonValue = None
    update: JsonValue = None
    isError: bool | None = None
    intent: str | None = None
    isTerminal: bool | None = None
    yielded: bool | None = None
    awaitingAsyncWork: bool | None = None

    @model_validator(mode="after")
    def validate_event(self) -> OmpEvent:
        match self.type:
            case (
                OmpEventType.MESSAGE_START
                | OmpEventType.MESSAGE_UPDATE
                | OmpEventType.MESSAGE_END
                | OmpEventType.TURN_END
            ):
                if self.message is None:
                    raise ValueError("OMP message event is missing its message")
            case OmpEventType.AGENT_END:
                if "messages" not in self.model_fields_set:
                    raise ValueError("OMP agent_end is missing its messages")
            case (
                OmpEventType.TOOL_START
                | OmpEventType.TOOL_UPDATE
                | OmpEventType.TOOL_STREAM
                | OmpEventType.TOOL_END
            ):
                if self.toolCallId is None or self.toolName is None:
                    raise ValueError("OMP tool event is missing its call identity")
        return self


class OmpModel(OmpWireModel):
    id: str
    provider: str
    name: str | None = None
    reasoning: bool | None = None
    contextWindow: int | None = None


class OmpState(OmpWireModel):
    sessionId: str
    sessionFile: str | None = None
    isSettled: bool
    model: OmpModel | None = None
    thinkingLevel: str | None = None
    isStreaming: bool = False
    hasPendingAsyncWork: bool = False
    queuedMessageCount: int = 0


class OmpHostToolCall(OmpWireModel):
    type: Literal["host_tool_call"] = "host_tool_call"
    id: str
    toolCallId: str
    toolName: str
    arguments: dict[str, JsonValue]


class OmpHostToolCancel(OmpWireModel):
    type: Literal["host_tool_cancel"] = "host_tool_cancel"
    id: str
    targetId: str


class OmpUIRequest(OmpWireModel):
    type: Literal["extension_ui_request"] = "extension_ui_request"
    id: str
    method: str
    title: str | None = None
    options: list[str] = Field(default_factory=list)
    message: str | None = None
    targetId: str | None = None


class OmpUIResponse(OmpWireModel):
    value: str | None = None
    confirmed: bool | None = None
    cancelled: bool = False

    @model_validator(mode="after")
    def validate_answer(self) -> OmpUIResponse:
        if (
            sum((self.value is not None, self.confirmed is not None, self.cancelled))
            > 1
        ):
            raise ValueError("OMP UI response must contain only one answer")
        return self


class OmpReady(OmpWireModel):
    type: Literal["ready"]
    protocolVersion: Literal[1]
    supportedProtocolVersions: list[int]
    maxFrameBytes: int = Field(gt=0, le=OMP_FRAME_BYTES)
    maxReassembledFrameBytes: int = Field(gt=0, le=OMP_REASSEMBLED_BYTES)


class OmpNegotiation(OmpWireModel):
    protocolVersion: Literal[2]


class OmpChunk(OmpWireModel):
    type: Literal["rpc_chunk"]
    chunkId: str = Field(min_length=1, max_length=128)
    index: int = Field(ge=0)
    count: int = Field(ge=2, le=OMP_REASSEMBLED_BYTES // OMP_CHUNK_BYTES)
    byteLength: int = Field(ge=OMP_FRAME_BYTES, le=OMP_REASSEMBLED_BYTES)
    data: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_index(self) -> OmpChunk:
        if self.index >= self.count:
            raise ValueError("OMP chunk index exceeds its count")
        return self


class OmpFrameError(OmpWireModel):
    type: Literal["rpc_frame_error"]
    error: str


class OmpResponse(OmpWireModel):
    type: Literal["response"]
    id: str | None = None
    command: str
    success: bool
    data: JsonValue = None
    error: str | None = None
    code: str | None = None


class OmpPromptAcceptance(OmpWireModel):
    agentInvoked: bool | None = None


class OmpPromptFailure(OmpWireModel):
    message: str
    retryable: bool
    provider: str | None = None
    model: str | None = None
    httpStatus: int | None = None


class OmpPromptResult(OmpWireModel):
    type: Literal["prompt_result"]
    id: str | None = None
    agentInvoked: bool
    status: Literal["completed", "error", "aborted"]
    sessionSettled: bool
    error: OmpPromptFailure | None = None


class OmpFrame(OmpWireModel):
    type: str


class OmpCommand(RootModel[dict[str, JsonValue]]):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)
