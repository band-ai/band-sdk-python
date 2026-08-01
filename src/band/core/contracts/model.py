"""ModelProvider request/response types."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import ConfigDict, Field
from band.core.bases import FrozenModel

from band.core.types import TurnUsage


class ModelMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ModelMessage(FrozenModel):
    """Provider-neutral chat message."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    role: ModelMessageRole
    content: Any
    tool_call_id: str | None = None
    name: str | None = None


class ModelSamplingOptions(FrozenModel):
    """Per-request sampling overrides. ``None`` = use instance default."""

    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, gt=0)


class ModelRequest(FrozenModel):
    """One completion call to a ``ModelProvider``.

    ``tools`` is a sequence of ``band.runtime.tools.ToolDefinition`` (typed as
    ``Any`` here so ``contracts`` does not import ``runtime`` at module load).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    messages: Sequence[ModelMessage] = Field(min_length=1)
    tools: Sequence[Any] | None = None
    system: str | None = None
    sampling: ModelSamplingOptions | None = None
    raw_options: Mapping[str, Any] | None = None


class ModelToolCall(FrozenModel):
    """A tool call requested by the model in one completion."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: Mapping[str, Any]


class ModelResponse(FrozenModel):
    """One completion response; per-call usage folds into ``TurnUsage``."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str | None = None
    tool_calls: Sequence[ModelToolCall] = ()
    usage: TurnUsage | None = None
    raw: Any = None
    stop_reason: str | None = None
