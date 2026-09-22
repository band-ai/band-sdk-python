"""OpenCode transport helpers."""

from __future__ import annotations

from band.integrations.opencode.client import (
    HttpOpencodeClient,
    OpencodeClientProtocol,
)
from band.integrations.opencode.events import (
    UNKNOWN_OPENCODE_ERROR,
    MessagePartDeltaEvent,
    MessagePartUpdatedEvent,
    MessageUpdatedEvent,
    OpencodeErrorInfo,
    OpencodeEvent,
    OpencodeMessageInfo,
    OpencodePart,
    OpencodePermissionRequest,
    OpencodeQuestion,
    OpencodeQuestionRequest,
    OpencodeTokens,
    OpencodeToolState,
    OpencodeToolStatus,
    PermissionAskedEvent,
    QuestionAskedEvent,
    SessionErrorEvent,
    SessionIdleEvent,
    UnknownOpencodeEvent,
    describe_error,
    parse_opencode_event,
)
from band.integrations.opencode.types import OpencodeSessionState

__all__ = [
    "UNKNOWN_OPENCODE_ERROR",
    "HttpOpencodeClient",
    "MessagePartDeltaEvent",
    "MessagePartUpdatedEvent",
    "MessageUpdatedEvent",
    "OpencodeClientProtocol",
    "OpencodeErrorInfo",
    "OpencodeEvent",
    "OpencodeMessageInfo",
    "OpencodePart",
    "OpencodePermissionRequest",
    "OpencodeQuestion",
    "OpencodeQuestionRequest",
    "OpencodeSessionState",
    "OpencodeTokens",
    "OpencodeToolState",
    "OpencodeToolStatus",
    "PermissionAskedEvent",
    "QuestionAskedEvent",
    "SessionErrorEvent",
    "SessionIdleEvent",
    "UnknownOpencodeEvent",
    "describe_error",
    "parse_opencode_event",
]
