"""Typed models for the OpenCode server's SSE events.

One parse boundary for the adapter's event loop: ``parse_opencode_event``
turns a raw SSE payload dict into a member of the ``OpencodeEvent`` union.
Anything that fails validation — an unknown ``type`` or a malformed payload
for a known type — degrades to ``UnknownOpencodeEvent``, never an exception,
so the event loop cannot be crashed by a payload.

Field shapes mirror OpenCode 1.18.4's wire format (camelCase keys are mapped
via aliases). Every field is optional with a default and every model allows
extra keys, so new server fields never break parsing; the adapter keeps its
semantic guards (e.g. skipping a request with no id) instead.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    ValidatorFunctionWrapHandler,
    field_validator,
)

from band.core.types import TurnUsage

logger = logging.getLogger(__name__)


class OpencodeModel(BaseModel):
    """Base for all OpenCode wire models: tolerant of unknown keys."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


def lenient(value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
    """Wrap-validator: a malformed *optional* nested model becomes ``None``.

    Applied to nested optional models so garbage in one corner of an event
    (e.g. ``tokens: "nope"``) degrades that corner instead of failing the
    whole event's parse — the rest of the event still drives the adapter.
    """
    try:
        return handler(value)
    except ValidationError:
        return None


def coerce_str_list(value: Any) -> list[str]:
    """Before-validator: junk becomes ``[]``; items stringify, ``None``s drop."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def coerce_dict(value: Any) -> dict[str, Any]:
    """Before-validator: a non-dict becomes ``{}``."""
    return value if isinstance(value, dict) else {}


class OpencodeTokenCache(OpencodeModel):
    read: int = 0
    write: int = 0


class OpencodeTokens(OpencodeModel):
    """An assistant message's ``info.tokens`` counters."""

    input: int = 0
    output: int = 0
    reasoning: int = 0
    cache: OpencodeTokenCache = Field(default_factory=OpencodeTokenCache)

    _coerce_cache = field_validator("cache", mode="before")(coerce_dict)

    def to_turn_usage(self) -> TurnUsage:
        """Map onto ``TurnUsage``.

        OpenCode reports reasoning tokens *disjointly* from output (its own
        total is ``input + output + reasoning + cache``), so fold reasoning
        into ``output_tokens`` — otherwise reasoning-heavy turns undercount,
        and this stays consistent with providers that already count reasoning
        inside output.
        """
        return TurnUsage(
            input_tokens=self.input,
            output_tokens=self.output + self.reasoning,
            cache_read_tokens=self.cache.read,
            cache_write_tokens=self.cache.write,
        )


class OpencodeErrorInfo(OpencodeModel):
    """An error reported on an assistant message or a session."""

    name: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    _coerce_data = field_validator("data", mode="before")(coerce_dict)

    def describe(self) -> str:
        name = self.name or "OpenCodeError"
        message = (self.data or {}).get("message")
        if message:
            return f"{name}: {message}"
        return f"{name}: OpenCode reported an error."


UNKNOWN_OPENCODE_ERROR = "OpenCode reported an unknown error."


def describe_error(error: OpencodeErrorInfo | None) -> str:
    """Human-readable form of an optional error payload."""
    if error is None:
        return UNKNOWN_OPENCODE_ERROR
    return error.describe()


class OpencodeMessageInfo(OpencodeModel):
    """The ``info`` half of a ``message.updated`` event."""

    id: str | None = None
    session_id: str | None = Field(default=None, alias="sessionID")
    role: str | None = None
    tokens: OpencodeTokens | None = None
    error: OpencodeErrorInfo | None = None

    _lenient_nested = field_validator("tokens", "error", mode="wrap")(lenient)


class OpencodeToolState(OpencodeModel):
    """A tool part's execution state."""

    status: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    error: Any = None

    _coerce_input = field_validator("input", mode="before")(coerce_dict)

    @property
    def has_output(self) -> bool:
        """Whether the wire payload carried an ``output`` key at all.

        Presence matters: a falsy-but-present output (``0``, ``""``) must be
        reported as-is, while an absent one reports as ``""``.
        """
        return "output" in self.model_fields_set

    @property
    def reported_output(self) -> Any:
        return self.output if self.has_output else ""


class OpencodePart(OpencodeModel):
    """A message part (``message.part.updated``).

    Deliberately one permissive model, not a tagged union: OpenCode has many
    part types (``step-start``, ``file``, ``patch``, …) the adapter must pass
    over silently, so an unknown part type must not be a parse failure. The
    adapter branches on ``type``.
    """

    id: str | None = None
    session_id: str | None = Field(default=None, alias="sessionID")
    message_id: str | None = Field(default=None, alias="messageID")
    type: str = ""
    text: str | None = None
    tool: str | None = None
    call_id: str | None = Field(default=None, alias="callID")
    state: OpencodeToolState | None = None

    _lenient_state = field_validator("state", mode="wrap")(lenient)


class OpencodePermissionToolRef(OpencodeModel):
    message_id: str | None = Field(default=None, alias="messageID")
    call_id: str | None = Field(default=None, alias="callID")


class OpencodePermissionRequest(OpencodeModel):
    """A ``permission.asked`` payload (OpenCode 1.18.4 shape).

    ``permission`` is the flat matcher key: the tool's registered name for a
    tool ask (e.g. ``band_store_memory``) or a built-in rule name such as
    ``doom_loop``.
    """

    id: str | None = None
    session_id: str | None = Field(default=None, alias="sessionID")
    permission: str = "unknown"
    patterns: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    always: list[str] = Field(default_factory=list)
    tool: OpencodePermissionToolRef | None = None

    _coerce_lists = field_validator("patterns", "always", mode="before")(
        coerce_str_list
    )
    _coerce_metadata = field_validator("metadata", mode="before")(coerce_dict)
    _lenient_tool = field_validator("tool", mode="wrap")(lenient)


class OpencodeQuestion(OpencodeModel):
    question: str = "Question"


class OpencodeQuestionRequest(OpencodeModel):
    """A ``question.asked`` payload."""

    id: str | None = None
    session_id: str | None = Field(default=None, alias="sessionID")
    questions: list[OpencodeQuestion] = Field(default_factory=list)

    @field_validator("questions", mode="before")
    @classmethod
    def _coerce_questions(cls, value: Any) -> list[Any]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]


class MessageUpdatedProps(OpencodeModel):
    info: OpencodeMessageInfo | None = None

    _lenient_info = field_validator("info", mode="wrap")(lenient)


class MessageUpdatedEvent(OpencodeModel):
    type: Literal["message.updated"]
    properties: MessageUpdatedProps = Field(default_factory=MessageUpdatedProps)

    @property
    def session_id(self) -> str | None:
        info = self.properties.info
        return info.session_id if info else None


class MessagePartUpdatedProps(OpencodeModel):
    part: OpencodePart | None = None

    _lenient_part = field_validator("part", mode="wrap")(lenient)


class MessagePartUpdatedEvent(OpencodeModel):
    type: Literal["message.part.updated"]
    properties: MessagePartUpdatedProps = Field(default_factory=MessagePartUpdatedProps)

    @property
    def session_id(self) -> str | None:
        part = self.properties.part
        return part.session_id if part else None


class MessagePartDeltaProps(OpencodeModel):
    session_id: str | None = Field(default=None, alias="sessionID")
    message_id: str | None = Field(default=None, alias="messageID")
    part_id: str | None = Field(default=None, alias="partID")
    field: str | None = None
    delta: str = ""


class MessagePartDeltaEvent(OpencodeModel):
    type: Literal["message.part.delta"]
    properties: MessagePartDeltaProps = Field(default_factory=MessagePartDeltaProps)

    @property
    def session_id(self) -> str | None:
        return self.properties.session_id


class PermissionAskedEvent(OpencodeModel):
    type: Literal["permission.asked"]
    properties: OpencodePermissionRequest = Field(
        default_factory=OpencodePermissionRequest
    )

    @property
    def session_id(self) -> str | None:
        return self.properties.session_id


class QuestionAskedEvent(OpencodeModel):
    type: Literal["question.asked"]
    properties: OpencodeQuestionRequest = Field(default_factory=OpencodeQuestionRequest)

    @property
    def session_id(self) -> str | None:
        return self.properties.session_id


class SessionErrorProps(OpencodeModel):
    session_id: str | None = Field(default=None, alias="sessionID")
    error: OpencodeErrorInfo | None = None

    _lenient_error = field_validator("error", mode="wrap")(lenient)


class SessionErrorEvent(OpencodeModel):
    type: Literal["session.error"]
    properties: SessionErrorProps = Field(default_factory=SessionErrorProps)

    @property
    def session_id(self) -> str | None:
        return self.properties.session_id


class SessionIdleProps(OpencodeModel):
    session_id: str | None = Field(default=None, alias="sessionID")


class SessionIdleEvent(OpencodeModel):
    type: Literal["session.idle"]
    properties: SessionIdleProps = Field(default_factory=SessionIdleProps)

    @property
    def session_id(self) -> str | None:
        return self.properties.session_id


class UnknownOpencodeEvent(OpencodeModel):
    """Fallback for event types the adapter does not consume, and for known
    types whose payload failed validation. Always ignored by the dispatcher."""

    type: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def session_id(self) -> None:
        return None


KnownOpencodeEvent = Annotated[
    Union[
        MessageUpdatedEvent,
        MessagePartUpdatedEvent,
        MessagePartDeltaEvent,
        PermissionAskedEvent,
        QuestionAskedEvent,
        SessionErrorEvent,
        SessionIdleEvent,
    ],
    Field(discriminator="type"),
]

OpencodeEvent = Union[KnownOpencodeEvent, UnknownOpencodeEvent]

_EVENT_ADAPTER: TypeAdapter[KnownOpencodeEvent] = TypeAdapter(KnownOpencodeEvent)


def parse_opencode_event(raw: dict[str, Any]) -> OpencodeEvent:
    """Parse one raw SSE payload into the typed event union.

    Never raises: an unrecognized ``type`` or a payload that fails validation
    yields ``UnknownOpencodeEvent`` (logged at debug), so a single bad event
    can never take down the adapter's event loop.
    """
    try:
        return _EVENT_ADAPTER.validate_python(raw)
    except ValidationError:
        logger.debug("Unparsed OpenCode event type=%s", raw.get("type"), exc_info=True)
        return UnknownOpencodeEvent(type=str(raw.get("type") or ""), raw=raw)
