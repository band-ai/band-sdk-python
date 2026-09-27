"""Credential redaction for failure data sent to external clients."""

from __future__ import annotations

import re
from typing import Any

_REDACTED = "[REDACTED]"
_CREDENTIAL_NAMES = r"token|authorization|api[_-]?key|access[_-]?key|secret|password"
_BEARER_TOKEN_RE = re.compile(r"Bearer\s+[^\s,;]+", re.IGNORECASE)
# Include spaces so a scheme-prefixed value is redacted in full.
_CREDENTIAL_KV_RE = re.compile(
    rf"({_CREDENTIAL_NAMES})\s*[:=]\s*[^,;]+",
    re.IGNORECASE,
)
_CREDENTIAL_FIELD_RE = re.compile(rf"(?:^|[_-])(?:{_CREDENTIAL_NAMES})$", re.IGNORECASE)
_CAMEL_CASE_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def redact_credentials(text: str) -> str:
    """Redact credential-shaped substrings in a message."""
    redacted = _BEARER_TOKEN_RE.sub(f"Bearer {_REDACTED}", text)
    return _CREDENTIAL_KV_RE.sub(
        lambda match: f"{match.group(1)}={_REDACTED}", redacted
    )


def _is_credential_field(key: str) -> bool:
    normalized = _CAMEL_CASE_BOUNDARY_RE.sub("_", key)
    return _CREDENTIAL_FIELD_RE.search(normalized) is not None


def redact_credentials_deep(value: Any) -> Any:
    """Redact nested strings, including dictionary keys."""
    match value:
        case str() as text:
            return redact_credentials(text)
        case dict() as mapping:
            redacted = {}
            for key, item in mapping.items():
                redacted_key = redact_credentials(key) if isinstance(key, str) else key
                redacted[redacted_key] = (
                    _REDACTED
                    if isinstance(key, str) and _is_credential_field(key)
                    else redact_credentials_deep(item)
                )
            return redacted
        case list() as items:
            return [redact_credentials_deep(item) for item in items]
        case _:
            return value
