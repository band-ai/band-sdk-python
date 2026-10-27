"""Recognize platform-authored deadline events on the authenticated delivery stream."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from band.core.types import metadata_to_dict

SYSTEM_EVENT_TYPE = "system"


class TerminalNotice(BaseModel):
    """The server's terminal marker, bound to one durable system-message identity.

    The platform verifies this row against its private session link before agent
    delivery. This parser does not grant authority to ordinary text metadata.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    profile: Literal["band-contract/terminal/1"]
    message: str
    session: str
    state: str = Field(min_length=1)
    outcome: str | None = Field(min_length=1)
    version: int = Field(gt=0, le=2**53 - 1)

    @field_validator("message", "session")
    @classmethod
    def canonical_uuid(cls, value: str) -> str:
        if str(UUID(value)) != value:
            raise ValueError("Expected a canonical UUID")
        return value


def is_terminal_delivery(
    message_type: str,
    message_id: str,
    metadata: object,
    recipient_id: str,
) -> bool:
    """Allow a targeted server system notice through the ordinary self-echo guard."""
    if message_type != SYSTEM_EVENT_TYPE:
        return False
    values = metadata_to_dict(metadata)
    if values.get("initiated_by") != SYSTEM_EVENT_TYPE:
        return False
    try:
        notice = TerminalNotice.model_validate(values.get("contract_terminal"))
    except ValidationError:
        return False
    if notice.message != message_id:
        return False
    mentions = values.get("mentions")
    return isinstance(mentions, list) and any(
        isinstance(mention, dict)
        and mention.get("id") == recipient_id
        and mention.get("kind", "mention") == "mention"
        for mention in mentions
    )
