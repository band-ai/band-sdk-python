"""Decode Band room failures for the ACP server boundary."""

from __future__ import annotations

from band_sdk_core import AgentFailure
from pydantic import JsonValue, TypeAdapter, ValidationError

from band.core.redaction import redact_credentials, redact_credentials_deep
from band.core.types import PlatformMessage, metadata_to_dict

_JSON_VALUE = TypeAdapter(JsonValue)


def decode_failure(msg: PlatformMessage) -> AgentFailure:
    """Invert the room failure shape from ``to_failure_event`` safely."""
    raw = metadata_to_dict(msg.metadata).get("failure")
    if isinstance(raw, dict):
        provider = raw.get("provider")
        message = raw.get("message")
        code = raw.get("code")
        if (
            isinstance(provider, str)
            and provider.strip()
            and isinstance(message, str)
            and message.strip()
            and (code is None or isinstance(code, str))
        ):
            try:
                detail = _JSON_VALUE.validate_python(raw.get("detail"))
            except ValidationError:
                pass
            else:
                try:
                    return AgentFailure(
                        provider,
                        redact_credentials(message),
                        code=code,
                        detail=redact_credentials_deep(detail),
                    )
                except (TypeError, ValueError):
                    pass
    return AgentFailure(
        "band",
        redact_credentials(msg.content.strip() or "Band peer reported a failure."),
    )
