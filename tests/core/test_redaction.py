"""Credential redaction for failure payloads sent to external clients."""

from band.core.redaction import redact_credentials_deep


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
