"""Credential redaction for failure data sent to external clients."""

from __future__ import annotations

import re
from typing import Any

_BEARER_TOKEN_RE = re.compile(r"Bearer\s+[^\s,;]+", re.IGNORECASE)
# Include spaces so a scheme-prefixed value is redacted in full.
_CREDENTIAL_KV_RE = re.compile(
    r"(token|authorization|api[_-]?key|access[_-]?key|secret|password)"
    r"\s*[:=]\s*[^,;]+",
    re.IGNORECASE,
)


def redact_credentials(text: str) -> str:
    """Redact credential-shaped substrings in a message."""
    redacted = _BEARER_TOKEN_RE.sub("Bearer [REDACTED]", text)
    return _CREDENTIAL_KV_RE.sub(r"\1=[REDACTED]", redacted)


def redact_credentials_deep(value: Any) -> Any:
    """Redact nested strings, including dictionary keys."""
    match value:
        case str() as text:
            return redact_credentials(text)
        case dict() as mapping:
            return {
                redact_credentials(key)
                if isinstance(key, str)
                else key: redact_credentials_deep(item)
                for key, item in mapping.items()
            }
        case list() as items:
            return [redact_credentials_deep(item) for item in items]
        case _:
            return value
