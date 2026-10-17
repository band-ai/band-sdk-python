"""PAP conversation and Operations envelopes, independent of an agent runtime."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langcodes import tag_is_valid
from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    model_validator,
)

from bandpap.discovery import HttpsUrl

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,256}$")]
Speaker = Literal["agent", "human"]
Status = Literal["working", "idle", "queued", "closed"]
Revision = Annotated[int, Field(strict=True, ge=1)]
JsonObject = dict[str, JsonValue]


def require_timestamp(value: object) -> object:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})",
        value,
    ):
        raise ValueError("Expected an RFC 3339 timestamp with an offset")
    return value


Timestamp = Annotated[AwareDatetime, BeforeValidator(require_timestamp)]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, allow_inf_nan=False)

    def wire(self) -> JsonObject:
        return self.model_dump(mode="json", exclude_unset=True)


class Context(WireModel):
    model_config = ConfigDict(extra="allow", frozen=True, allow_inf_nan=False)
    locale: str | None = None
    time_zone: str | None = None
    user_available: Annotated[bool, Field(strict=True)] | None = None

    @model_validator(mode="before")
    @classmethod
    def require_json(cls, value: object) -> object:
        if isinstance(value, cls):
            return value
        data = TypeAdapter(JsonObject).validate_python(value)
        for field in ("locale", "time_zone", "user_available"):
            if field in data and data[field] is None:
                raise ValueError(f"Context {field} cannot be null")
        locale = data.get("locale")
        if locale is not None and (
            not isinstance(locale, str) or "_" in locale or not tag_is_valid(locale)
        ):
            raise ValueError("Context locale must be a BCP 47 language tag")
        zone = data.get("time_zone")
        if zone is not None:
            if not isinstance(zone, str):
                raise ValueError("Context time_zone must be an IANA timezone")
            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ValueError(
                    "Context time_zone must be an IANA timezone"
                ) from error
        return data


class Message(WireModel):
    id: Identifier
    sender: Speaker
    text: str | None = None
    data: JsonObject | None = None
    context: Context | None = None

    @model_validator(mode="after")
    def require_content(self) -> Self:
        if self.text is None and self.data is None and self.context is None:
            raise ValueError("Message requires text, data or context")
        return self


class SendMessage(WireModel):
    message: Message


class DirectConversation(SendMessage):
    parent_conversation_id: Identifier


class ConversationResponse(WireModel):
    conversation_id: Identifier
    status: Status
    responder: Speaker


class ReadQuery(WireModel):
    cursor: Identifier | None = None
    wait: Annotated[int, Field(strict=True, ge=0)] = 0

    def resume_cursor(self, last_event_id: str | None = None) -> str | None:
        if last_event_id is None:
            return self.cursor
        return ReadQuery(cursor=last_event_id).cursor


class Confirmation(WireModel):
    revision: Revision
    approved_by: Literal["user", "standing_permission"]
    confirmed_at: Timestamp


class ConfirmOperation(WireModel):
    revision: Revision
    approved_by: Literal["user", "standing_permission"]


class OperationResult(WireModel):
    summary: str
    data: JsonObject | None = None


class Operation(WireModel):
    operation_id: str = Field(min_length=1)
    revision: Revision
    state: Literal[
        "proposed", "in_progress", "succeeded", "failed", "cancelled", "expired"
    ]
    summary: str
    terms: JsonObject | None = None
    expires_at: Timestamp | None = None
    user_approval_required: Annotated[bool, Field(strict=True)] = False
    url: HttpsUrl | None = None
    confirmation: Confirmation | None
    result: OperationResult | None

    @model_validator(mode="after")
    def require_state_fields(self) -> Self:
        if self.state == "proposed" and (
            self.expires_at is None or self.confirmation is not None
        ):
            raise ValueError("Proposed operation needs expiry and no confirmation")
        if (
            self.state in {"in_progress", "succeeded", "failed"}
            and self.confirmation is None
        ):
            raise ValueError("Confirmed operation needs confirmation")
        if self.state == "expired" and self.confirmation is not None:
            raise ValueError("Expired operation cannot be confirmed")
        if (
            self.confirmation is not None
            and self.confirmation.revision != self.revision
        ):
            raise ValueError("Confirmation must describe this revision")
        if self.state in {"proposed", "in_progress"} and self.result is not None:
            raise ValueError("Nonfinal operation cannot have a result")
        if self.state not in {"proposed", "in_progress"} and self.result is None:
            raise ValueError("Final operation needs a result")
        return self


class RecordedMessage(WireModel):
    id: Identifier
    role: Literal["user", "company"]
    sender: Speaker
    text: str | None = None
    data: JsonObject | None = None
    context: Context | None = None
    operation: Operation | None = None

    @model_validator(mode="after")
    def require_content(self) -> Self:
        if all(
            item is None
            for item in (self.text, self.data, self.context, self.operation)
        ):
            raise ValueError("Recorded message requires content")
        if self.role == "company" and self.context is not None:
            raise ValueError("Only user-side messages carry context")
        return self


class Event(WireModel):
    id: Identifier
    type: str
    created_at: Timestamp


class MessageEvent(Event):
    type: Literal["message"]
    message: RecordedMessage


class StateEvent(Event):
    type: Literal["state"]
    status: Status
    responder: Speaker


class AuthorizationEvent(Event):
    type: Literal["authorization"]
    error: Literal["sign_in_required", "insufficient_scope"]
    scope: str | None = None

    @model_validator(mode="after")
    def require_scope(self) -> Self:
        if self.error == "insufficient_scope" and not self.scope:
            raise ValueError("Insufficient scope event must name required scopes")
        return self


class UserRequestedEvent(Event):
    type: Literal["user_requested"]
    reason: str


class DirectEvent(Event):
    type: Literal["direct_opened", "direct_closed"]
    conversation_id: Identifier


EVENT_MODELS: dict[str, type[Event]] = {
    "message": MessageEvent,
    "state": StateEvent,
    "authorization": AuthorizationEvent,
    "user_requested": UserRequestedEvent,
    "direct_opened": DirectEvent,
    "direct_closed": DirectEvent,
}


def parse_event(value: object) -> Event:
    if isinstance(value, Event):
        return value
    if not isinstance(value, dict):
        raise ValueError("Expected an event object")
    kind = value.get("type")
    if not isinstance(kind, str):
        raise ValueError("Expected an event type")
    return EVENT_MODELS.get(kind, Event).model_validate(value)


ParsedEvent = Annotated[Event, BeforeValidator(parse_event)]


class EventPage(ConversationResponse):
    events: tuple[ParsedEvent, ...]
    cursor: Identifier | None
    has_more: Annotated[bool, Field(strict=True)]

    @model_validator(mode="after")
    def require_last_cursor(self) -> Self:
        if self.events and self.cursor != self.events[-1].id:
            raise ValueError("Page cursor must be its last event ID")
        if len({event.id for event in self.events}) != len(self.events):
            raise ValueError("Page has duplicate event IDs")
        return self

    def wire(self) -> JsonObject:
        return self.model_dump(mode="json", exclude_unset=True, serialize_as_any=True)


class TextDelta(WireModel):
    message_id: Identifier
    text: str


class ErrorBody(WireModel):
    error: str = Field(min_length=1)
    error_description: str | None = None
    scope: str | None = None
    extension: str | None = None
    conversation_id: Identifier | None = None
    operation: Operation | None = None
