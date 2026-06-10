"""Slack request signature verification.

Implements HMAC-SHA256 over Slack request bodies per
https://docs.slack.dev/authentication/verifying-requests-from-slack, plus
timestamp replay protection (reject requests outside
``MAX_TIMESTAMP_SKEW_SECONDS``).
"""

from __future__ import annotations

import hashlib
import hmac
import time

MAX_TIMESTAMP_SKEW_SECONDS = 5 * 60
SLACK_SIGNATURE_VERSION = "v0"


def verify_signature(
    *,
    signing_secret: str,
    body: bytes,
    timestamp: str,
    signature: str,
    now: float | None = None,
) -> bool:
    """Verify a Slack request signature.

    Returns ``False`` on any failure — bad inputs, expired timestamp,
    malformed signature, or HMAC mismatch — so callers can treat the
    result as a single boolean gate without juggling exception types.

    Args:
        signing_secret: The Slack app's signing secret.
        body: Raw request body bytes (must be unmodified — JSON parsing
            re-encodes and breaks the signature).
        timestamp: Value of the ``X-Slack-Request-Timestamp`` header.
        signature: Value of the ``X-Slack-Signature`` header
            (``"v0=<hex>"`` format).
        now: Override current epoch seconds (for tests). Defaults to
            ``time.time()``.

    Returns:
        True iff the signature verifies and the timestamp is within
        ``MAX_TIMESTAMP_SKEW_SECONDS`` of ``now``.
    """
    if not signing_secret or not timestamp or not signature:
        return False

    try:
        ts_int = int(timestamp)
    except ValueError:
        return False

    current = time.time() if now is None else now
    if abs(current - ts_int) > MAX_TIMESTAMP_SKEW_SECONDS:
        return False

    basestring = f"{SLACK_SIGNATURE_VERSION}:{timestamp}:".encode() + body
    expected_digest = hmac.new(
        signing_secret.encode(),
        basestring,
        hashlib.sha256,
    ).hexdigest()
    expected_signature = f"{SLACK_SIGNATURE_VERSION}={expected_digest}"

    return hmac.compare_digest(expected_signature, signature)
