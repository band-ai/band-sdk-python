"""OpenCode transport helpers."""

from __future__ import annotations

from band.integrations.opencode.client import (
    HttpOpencodeClient,
    OpencodeClientProtocol,
)
from band.integrations.opencode.events import (
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
    "PermissionAskedEvent",
    "QuestionAskedEvent",
    "SessionErrorEvent",
    "SessionIdleEvent",
    "UnknownOpencodeEvent",
    "describe_error",
    "parse_opencode_event",
]
