"""Tests for redacting/capping an internal exception message before it
reaches an audience outside this process."""

from __future__ import annotations

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


def test_redact_credentials_covers_bearer_tokens_and_key_value_pairs() -> None:
    redacted = redact_credentials(
        "upstream rejected Bearer abc123.def456 (api_key=sk-live-secret)"
    )
    assert "Bearer [REDACTED]" in redacted
    assert "api_key=[REDACTED]" in redacted
    assert "abc123.def456" not in redacted
    assert "sk-live-secret" not in redacted
