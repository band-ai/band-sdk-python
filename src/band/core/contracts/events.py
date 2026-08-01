"""Turn-event vocabulary for the runtime event sink."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import ConfigDict, Field
from band.core.bases import FrozenModel


class TurnEventKind(StrEnum):
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TEXT_DELTA = "text_delta"
    ERROR = "error"
    PLAN = "plan"
    RUN_FAILED = "run_failed"


class ToolStatus(StrEnum):
    """Tool-call lifecycle status on turn events and ACP tool chunks.

    Shared vocabulary for in-process ``EventSink`` emission and ACP
    ``CollectedChunk`` metadata — keep one spelling so delivery checks and
    room emitters compare the same values.
    """

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ThoughtEvent(FrozenModel):
    kind: Literal[TurnEventKind.THOUGHT] = TurnEventKind.THOUGHT
    content: str


class ToolCallEvent(FrozenModel):
    kind: Literal[TurnEventKind.TOOL_CALL] = TurnEventKind.TOOL_CALL
    tool_name: str = Field(min_length=1)
    tool_call_id: str | None = None
    arguments: Mapping[str, Any] | None = None
    status: ToolStatus | None = None


class ToolResultEvent(FrozenModel):
    kind: Literal[TurnEventKind.TOOL_RESULT] = TurnEventKind.TOOL_RESULT
    tool_name: str = Field(min_length=1)
    tool_call_id: str | None = None
    content: str
    status: ToolStatus | None = None


class TextDeltaEvent(FrozenModel):
    kind: Literal[TurnEventKind.TEXT_DELTA] = TurnEventKind.TEXT_DELTA
    content: str


class ErrorEvent(FrozenModel):
    kind: Literal[TurnEventKind.ERROR] = TurnEventKind.ERROR
    content: str


class PlanEvent(FrozenModel):
    kind: Literal[TurnEventKind.PLAN] = TurnEventKind.PLAN
    content: str


class RunFailedEvent(FrozenModel):
    """Model/execution failure observed on the stream (does not raise)."""

    kind: Literal[TurnEventKind.RUN_FAILED] = TurnEventKind.RUN_FAILED
    message: str
    retryable: bool = False
    error_type: str | None = None
    partial_text: str | None = None


TurnEvent = (
    ThoughtEvent
    | ToolCallEvent
    | ToolResultEvent
    | TextDeltaEvent
    | ErrorEvent
    | PlanEvent
    | RunFailedEvent
)


class EnvelopedTurnEvent(FrozenModel):
    """Sink-assigned envelope (``run_id``, monotonic ``sequence``, ``timestamp``)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    timestamp: float
    event: TurnEvent = Field(discriminator="kind")
