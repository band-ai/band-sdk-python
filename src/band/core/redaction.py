"""Credential redaction for failure data sent to external clients."""

from __future__ import annotations

import re
from typing import Any

_REDACTED = "[REDACTED]"
# Names whose ``name: value`` in free text is a credential value.
_CREDENTIAL_NAMES = (
    r"token|authorization|api[_-]?key|access[_-]?key|secret(?:[_-]?key)?"
    r"|password|passwd|private[_-]?key|session[_-]?key"
)
# In prose these usually introduce an explanation ("Invalid credentials: ..."),
# so they are matched only as field names.
_CREDENTIAL_FIELD_NAMES = rf"{_CREDENTIAL_NAMES}|auth|credential|cookie"
_AUTH_SCHEME_VALUE_RE = re.compile(r"\b(Bearer|Basic)\s+[^\s,;]+", re.IGNORECASE)
# Include spaces so a scheme-prefixed value is redacted in full.
_CREDENTIAL_KV_RE = re.compile(
    rf"({_CREDENTIAL_NAMES})\s*[:=]\s*[^,;]+",
    re.IGNORECASE,
)
# Matched against the key with separators removed, so ``apiKey``, ``api-key`` and
# ``OpenAIAPIKey`` all end in ``apikey``. End-anchored so a non-secret such as
# ``token_count`` is left intact.
_CREDENTIAL_FIELD_RE = re.compile(
    rf"(?:{_CREDENTIAL_FIELD_NAMES})s?(?:value)?$",
    re.IGNORECASE,
)
_KEY_SEPARATOR_RE = re.compile(r"[_-]")


def redact_credentials(text: str) -> str:
    """Redact credential-shaped substrings in a message."""
    redacted = _AUTH_SCHEME_VALUE_RE.sub(
        lambda match: f"{match.group(1)} {_REDACTED}", text
    )
    return _CREDENTIAL_KV_RE.sub(
        lambda match: f"{match.group(1)}={_REDACTED}", redacted
    )


def _assign_redacted(redacted: dict[Any, Any], key: Any, item: Any) -> None:
    """Store one entry, keeping a distinct key when redaction collides."""
    if key not in redacted:
        redacted[key] = item
        return
    suffix = 2
    while f"{key}#{suffix}" in redacted:
        suffix += 1
    redacted[f"{key}#{suffix}"] = item


def _is_credential_field(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    return _CREDENTIAL_FIELD_RE.search(_KEY_SEPARATOR_RE.sub("", key)) is not None


def _redact_field_value(key: Any, item: Any) -> Any:
    """Hide a credential field's value; flags and counts keep their type."""
    if _is_credential_field(key) and isinstance(item, str | dict | list):
        return _REDACTED
    return redact_credentials_deep(item)


def redact_credentials_deep(value: Any) -> Any:
    """Redact nested strings, including dictionary keys."""
    match value:
        case str() as text:
            return redact_credentials(text)
        case dict() as mapping:
            redacted: dict[Any, Any] = {}
            for key, item in mapping.items():
                redacted_key = redact_credentials(key) if isinstance(key, str) else key
                _assign_redacted(redacted, redacted_key, _redact_field_value(key, item))
            return redacted
        case list() as items:
            return [redact_credentials_deep(item) for item in items]
        case _:
            return value
