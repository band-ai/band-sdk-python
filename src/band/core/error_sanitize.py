"""Redacting/capping an internal exception message before it reaches an
audience outside this process -- an external API client, a chat room, or
anywhere else a raw ``str(exception)`` would otherwise leak credentials or
implementation detail nobody outside asked for.
"""

from __future__ import annotations

import re

_MAX_CHARS = 240
_BEARER_TOKEN_RE = re.compile(r"Bearer\s+[^\s,;]+", re.IGNORECASE)
# The value group excludes only "," and ";" (not whitespace) so a
# scheme-prefixed credential (e.g. "Authorization: ApiKey sk-...") gets
# redacted in full instead of leaking everything past the first space.
_CREDENTIAL_KV_RE = re.compile(
    r"(token|authorization|api[_-]?key|access[_-]?key|secret|password)"
    r"\s*[:=]\s*[^,;]+",
    re.IGNORECASE,
)


def redact_credentials(text: str) -> str:
    """Redact bearer tokens/API keys a message may embed."""
    redacted = _BEARER_TOKEN_RE.sub("Bearer [REDACTED]", text)
    return _CREDENTIAL_KV_RE.sub(r"\1=[REDACTED]", redacted)


def sanitize_external_error_message(exc: BaseException) -> str:
    """Redact credentials from *exc*'s message and cap its length.

    Mirrors the TS SDK's ``sanitizeGatewayErrorMessage``.
    """
    trimmed = str(exc).strip()
    if not trimmed:
        return "Unknown error"
    redacted = redact_credentials(trimmed)
    if len(redacted) <= _MAX_CHARS:
        return redacted
    return f"{redacted[: _MAX_CHARS - 3]}..."
