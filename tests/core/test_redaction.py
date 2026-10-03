"""Credential redaction for failure payloads sent to external clients."""

from __future__ import annotations

import pytest

from band.core.redaction import redact_credentials, redact_credentials_deep


@pytest.mark.parametrize(
    "prose",
    [
        "Invalid credentials: check BAND_API_KEY, then retry",
        "Could not read cookies: permission denied",
    ],
)
def test_credential_words_in_prose_stay_readable(prose: str) -> None:
    assert redact_credentials(prose) == prose


def test_basic_auth_value_is_redacted() -> None:
    assert redact_credentials("sent Basic dXNlcjpwYXNz upstream") == (
        "sent Basic [REDACTED] upstream"
    )


@pytest.mark.parametrize(
    "field",
    ["tokenValue", "auth", "OpenAIAPIKey", "passwd", "api_key_value", "refresh_tokens"],
)
def test_common_credential_field_spellings_are_redacted(field: str) -> None:
    assert redact_credentials_deep({field: "leak"}) == {field: "[REDACTED]"}


def test_scalar_flags_under_credential_names_keep_their_type() -> None:
    assert redact_credentials_deep({"has_credentials": True, "max_token": 5}) == {
        "has_credentials": True,
        "max_token": 5,
    }


def test_credential_field_suffixes_redact_the_whole_value() -> None:
    redacted = redact_credentials_deep(
        {
            "secret_key": "s",
            "private_key": "x",
            "credentials": {"u": "p"},
            "cookie": "sid=1",
            "session_key": "sess",
            "apiKey": "k",
            "client_secret": "c",
        }
    )

    assert redacted == {
        "secret_key": "[REDACTED]",
        "private_key": "[REDACTED]",
        "credentials": "[REDACTED]",
        "cookie": "[REDACTED]",
        "session_key": "[REDACTED]",
        "apiKey": "[REDACTED]",
        "client_secret": "[REDACTED]",
    }


def test_non_credential_fields_stay_visible() -> None:
    assert redact_credentials_deep({"token_count": 12, "session_id": "abc"}) == {
        "token_count": 12,
        "session_id": "abc",
    }


def test_redacted_keys_that_collide_are_both_kept() -> None:
    assert redact_credentials_deep({"token: a": 1, "token: b": 2}) == {
        "token=[REDACTED]": 1,
        "token=[REDACTED]#2": 2,
    }
