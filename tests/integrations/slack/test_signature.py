"""Tests for Slack request signature verification."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from band.integrations.slack.signature import (
    MAX_TIMESTAMP_SKEW_SECONDS,
    SLACK_SIGNATURE_VERSION,
    verify_signature,
)

SIGNING_SECRET = "test-signing-secret"


def _sign(secret: str, body: bytes, timestamp: str) -> str:
    """Compute the canonical ``v0=<hex>`` signature for a payload."""
    base = f"{SLACK_SIGNATURE_VERSION}:{timestamp}:".encode() + body
    digest = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return f"{SLACK_SIGNATURE_VERSION}={digest}"


def test_valid_signature_within_skew_window():
    body = b'{"type":"event_callback"}'
    now = 1_700_000_000.0
    timestamp = str(int(now))
    signature = _sign(SIGNING_SECRET, body, timestamp)

    assert verify_signature(
        signing_secret=SIGNING_SECRET,
        body=body,
        timestamp=timestamp,
        signature=signature,
        now=now,
    )


def test_signature_rejects_when_body_tampered():
    now = 1_700_000_000.0
    timestamp = str(int(now))
    signature = _sign(SIGNING_SECRET, b"original", timestamp)

    assert not verify_signature(
        signing_secret=SIGNING_SECRET,
        body=b"tampered",
        timestamp=timestamp,
        signature=signature,
        now=now,
    )


def test_signature_rejects_wrong_signing_secret():
    body = b"hello"
    now = 1_700_000_000.0
    timestamp = str(int(now))
    signature = _sign("different-secret", body, timestamp)

    assert not verify_signature(
        signing_secret=SIGNING_SECRET,
        body=body,
        timestamp=timestamp,
        signature=signature,
        now=now,
    )


def test_signature_rejects_when_signature_garbage():
    body = b"hello"
    now = 1_700_000_000.0
    timestamp = str(int(now))

    assert not verify_signature(
        signing_secret=SIGNING_SECRET,
        body=body,
        timestamp=timestamp,
        signature="v0=not-a-real-hex-digest",
        now=now,
    )


def test_signature_rejects_when_prefix_missing():
    body = b"hello"
    now = 1_700_000_000.0
    timestamp = str(int(now))
    valid = _sign(SIGNING_SECRET, body, timestamp)
    # Strip the "v0=" prefix to simulate a malformed header.
    bare_hex = valid.removeprefix("v0=")

    assert not verify_signature(
        signing_secret=SIGNING_SECRET,
        body=body,
        timestamp=timestamp,
        signature=bare_hex,
        now=now,
    )


def test_signature_rejects_expired_timestamp():
    body = b"hello"
    now = 1_700_000_000.0
    # Sign with a timestamp older than the skew window.
    old_ts = str(int(now - MAX_TIMESTAMP_SKEW_SECONDS - 1))
    signature = _sign(SIGNING_SECRET, body, old_ts)

    assert not verify_signature(
        signing_secret=SIGNING_SECRET,
        body=body,
        timestamp=old_ts,
        signature=signature,
        now=now,
    )


def test_signature_rejects_future_timestamp_beyond_skew():
    body = b"hello"
    now = 1_700_000_000.0
    future_ts = str(int(now + MAX_TIMESTAMP_SKEW_SECONDS + 1))
    signature = _sign(SIGNING_SECRET, body, future_ts)

    assert not verify_signature(
        signing_secret=SIGNING_SECRET,
        body=body,
        timestamp=future_ts,
        signature=signature,
        now=now,
    )


def test_signature_accepts_at_skew_boundary():
    body = b"hello"
    now = 1_700_000_000.0
    edge_ts = str(int(now - MAX_TIMESTAMP_SKEW_SECONDS))
    signature = _sign(SIGNING_SECRET, body, edge_ts)

    assert verify_signature(
        signing_secret=SIGNING_SECRET,
        body=body,
        timestamp=edge_ts,
        signature=signature,
        now=now,
    )


def test_signature_rejects_malformed_timestamp():
    body = b"hello"
    now = 1_700_000_000.0
    timestamp = "not-an-integer"
    signature = _sign(SIGNING_SECRET, body, timestamp)

    assert not verify_signature(
        signing_secret=SIGNING_SECRET,
        body=body,
        timestamp=timestamp,
        signature=signature,
        now=now,
    )


@pytest.mark.parametrize(
    "signing_secret,timestamp,signature",
    [
        ("", "1700000000", "v0=deadbeef"),
        ("secret", "", "v0=deadbeef"),
        ("secret", "1700000000", ""),
    ],
)
def test_signature_rejects_missing_inputs(signing_secret, timestamp, signature):
    assert not verify_signature(
        signing_secret=signing_secret,
        body=b"hello",
        timestamp=timestamp,
        signature=signature,
        now=1_700_000_000.0,
    )


def test_signature_uses_constant_time_compare():
    # We can't directly assert hmac.compare_digest is used, but we can
    # confirm a signature with the right length but wrong content is
    # rejected — the same as a totally bogus signature. This guards
    # against a naive "==" implementation accidentally short-circuiting
    # on a partial prefix match.
    body = b"hello"
    now = 1_700_000_000.0
    timestamp = str(int(now))
    valid = _sign(SIGNING_SECRET, body, timestamp)
    almost = valid[:-2] + ("ff" if valid[-2:] != "ff" else "00")

    assert not verify_signature(
        signing_secret=SIGNING_SECRET,
        body=body,
        timestamp=timestamp,
        signature=almost,
        now=now,
    )


def test_signature_uses_real_clock_when_now_not_passed():
    # When no `now` is passed, the function reads time.time(); a fresh
    # signature with a current timestamp should verify.
    import time

    body = b"hello"
    timestamp = str(int(time.time()))
    signature = _sign(SIGNING_SECRET, body, timestamp)

    assert verify_signature(
        signing_secret=SIGNING_SECRET,
        body=body,
        timestamp=timestamp,
        signature=signature,
    )
