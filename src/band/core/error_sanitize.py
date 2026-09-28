"""Redacting/capping an internal exception message before it reaches an
audience outside this process -- an external API client, a chat room, or
anywhere else a raw ``str(exception)`` would otherwise leak credentials or
implementation detail nobody outside asked for.
"""

from __future__ import annotations

import re

_MAX_CHARS = 240
# Both value groups run to end-of-line rather than stopping at "," or ";" --
# a real credential can itself contain either (e.g. AWS SigV4's
# "Authorization: AWS4-HMAC-SHA256 Credential=..., SignedHeaders=a;b, Signature=..."
# is one credential value, comma- and semicolon-delimited internally), so a
# narrower value group leaks everything past the first one. Over-redacting
# the rest of the line is an acceptable trade for never leaking a secret;
# length is already capped separately by sanitize_external_error_message.
_BEARER_TOKEN_RE = re.compile(r"Bearer\s+\S.*", re.IGNORECASE)
_CREDENTIAL_KV_RE = re.compile(
    r"(token|authorization|api[_-]?key|access[_-]?key|secret|password)"
    r"\s*[:=]\s*.+",
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
