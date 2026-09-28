"""Tests for redacting/capping an internal exception message before it
reaches an audience outside this process."""

from __future__ import annotations

import pytest

from band.core.error_sanitize import redact_credentials, sanitize_external_error_message


def test_empty_message_falls_back_to_unknown_error() -> None:
    assert sanitize_external_error_message(ValueError("")) == "Unknown error"
    assert sanitize_external_error_message(ValueError("   ")) == "Unknown error"


def test_short_message_is_returned_unchanged() -> None:
    assert sanitize_external_error_message(ValueError("connection refused")) == (
        "connection refused"
    )


def test_long_message_is_capped_to_240_chars_with_ellipsis() -> None:
    message = sanitize_external_error_message(ValueError("x" * 300))
    assert len(message) == 240
    assert message.endswith("...")
    assert message == "x" * 237 + "..."


def test_credential_past_the_truncation_boundary_is_still_redacted() -> None:
    """Redaction must run before truncation, not after -- otherwise a
    credential landing past the 240-char cutoff would leak in full."""
    secret = "s" * 50
    message = sanitize_external_error_message(
        ValueError("x" * 225 + f"password={secret}")
    )
    assert secret not in message
    assert len(message) == 240


def test_redact_credentials_covers_bearer_tokens() -> None:
    redacted = redact_credentials("upstream rejected Bearer abc123.def456")
    assert redacted == "upstream rejected Bearer [REDACTED]"
    assert "abc123.def456" not in redacted


def test_redact_credentials_covers_key_value_pairs() -> None:
    redacted = redact_credentials("upstream rejected (api_key=sk-live-secret)")
    assert "api_key=[REDACTED]" in redacted
    assert "sk-live-secret" not in redacted


def test_redact_credentials_value_containing_comma_or_semicolon_is_fully_redacted() -> (
    None
):
    """A credential value that itself contains a comma or semicolon (e.g.
    AWS SigV4's "Credential=..., SignedHeaders=a;b, Signature=..." is one
    Authorization value, delimited internally by both) must not leak
    anything past the first one -- the whole rest of the line is
    considered part of the credential."""
    redacted = redact_credentials(
        "Authorization: AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/20240101/"
        "us-east-1/ec2/aws4_request, SignedHeaders=host;x-amz-date, "
        "Signature=abcdef0123456789"
    )
    assert "AKIAEXAMPLE" not in redacted
    assert "abcdef0123456789" not in redacted
    assert redacted == "Authorization=[REDACTED]"

    redacted = redact_credentials("rejected Bearer tok_abc,tok_xyz for this request")
    assert "tok_xyz" not in redacted
    assert redacted == "rejected Bearer [REDACTED]"


def test_redact_credentials_full_value_scheme_prefixed() -> None:
    """A scheme-prefixed credential value (a space between the key and the
    secret) must be redacted in full, not just up to that space."""
    redacted = redact_credentials("Authorization: ApiKey sk-live-abcdef123456")
    assert "sk-live-abcdef123456" not in redacted
    assert redacted == "Authorization=[REDACTED]"


@pytest.mark.parametrize(
    "text",
    [
        "password=hunter2",
        "client_secret=abc123XYZ",
        "AWS_SECRET_ACCESS_KEY=AKIAABCDEFGHIJKLMNOP",
    ],
)
def test_redact_credentials_covers_non_token_keywords(text: str) -> None:
    """token/authorization/api_key aren't the only credential-shaped
    keywords an exception message can embed -- password, secret (and its
    client_secret compound), and access_key must be redacted too."""
    redacted = redact_credentials(text)
    secret_value = text.split("=", 1)[1]
    assert secret_value not in redacted
