"""Credential redaction for failure payloads sent to external clients."""

from __future__ import annotations

import pytest

from band.core.redaction import redact_credentials, redact_credentials_deep

REDACTION_CASES = [
    pytest.param(
        "Authorization: AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/x, "
        "SignedHeaders=host;x-amz-date, Signature=abcdef0123",
        "Authorization=[REDACTED]",
        id="sigv4-header",
    ),
    pytest.param(
        "Authorization: ApiKey sk-live-abcdef123456",
        "Authorization=[REDACTED]",
        id="scheme-prefixed-value",
    ),
    pytest.param(
        "upstream rejected Bearer abc123.def456 (api_key=sk-live-secret)",
        "upstream rejected Bearer [REDACTED] (api_key=[REDACTED]",
        id="unlabeled-bearer-and-key",
    ),
    pytest.param(
        "sent Basic dXNlcjpwYXNz upstream",
        "sent Basic [REDACTED] upstream",
        id="basic-auth",
    ),
    pytest.param("password=hunter2", "password=[REDACTED]", id="password"),
    pytest.param("client_secret=abc123XYZ", "client_secret=[REDACTED]", id="secret"),
    pytest.param(
        "AWS_SECRET_ACCESS_KEY=AKIAABCDEFGHIJKLMNOP",
        "AWS_SECRET_ACCESS_KEY=[REDACTED]",
        id="access-key-env",
    ),
    pytest.param(
        "password: alpha,beta;gamma", "password=[REDACTED]", id="delimited-value"
    ),
    pytest.param(
        "Invalid API key: sk-live-abc123", "Invalid API key=[REDACTED]", id="api key"
    ),
    pytest.param("apikey: v", "apikey=[REDACTED]", id="apikey"),
    pytest.param("Api-Key: v", "Api-Key=[REDACTED]", id="api-key"),
    pytest.param(
        "Invalid access key: short-test-value",
        "Invalid access key=[REDACTED]",
        id="access key",
    ),
    pytest.param("secret key: s3cr3t", "secret key=[REDACTED]", id="secret key"),
    pytest.param("Private Key: v", "Private Key=[REDACTED]", id="private key"),
    pytest.param("session key: v", "session key=[REDACTED]", id="session key"),
    pytest.param("Session-Key: v", "Session-Key=[REDACTED]", id="session-key"),
    pytest.param(
        '{"api_key": "sk-abc", "x": 1}', '{"api_key=[REDACTED]', id="json-body"
    ),
    pytest.param("{'token': 'abc'}", "{'token=[REDACTED]", id="dict-repr"),
    pytest.param("api_key:\n  sk-live-abc", "api_key=[REDACTED]", id="value-next-lf"),
    pytest.param("password: \r\nnext", "password=[REDACTED]", id="value-next-crlf"),
    pytest.param(
        "token:\nabc\nretry later",
        "token=[REDACTED]\nretry later",
        id="value-next-keeps-later-lines",
    ),
    pytest.param(
        "api_key:\n\n  sk-abc", "api_key=[REDACTED]", id="value-after-blank-line"
    ),
    pytest.param(
        "password:\n\nnext\nretry later",
        "password=[REDACTED]\nretry later",
        id="blank-line-keeps-later-lines",
    ),
    pytest.param(
        "token: a\r\nretry later\napi key: b\rdone",
        "token=[REDACTED]\r\nretry later\napi key=[REDACTED]\rdone",
        id="multiline",
    ),
    pytest.param(
        "Authorization: Bearer\n  abc123",
        "Authorization=[REDACTED]",
        id="bearer-value-next-line",
    ),
    pytest.param("Bearer\nabc\nnext", "Bearer [REDACTED]\nnext", id="bearer-next"),
    pytest.param("Bearer\n\nabc123", "Bearer [REDACTED]", id="bearer-after-blank-line"),
    pytest.param("Bearerless: ok", "Bearerless: ok", id="bearer-prefix-word"),
    pytest.param("password:\u00a0s3cr3t", "password=[REDACTED]", id="nbsp-after-delim"),
    pytest.param("token\x0c: s3cr3t", "token=[REDACTED]", id="form-feed-before-delim"),
    pytest.param("token:\u2028s3cr3t", "token=[REDACTED]", id="unicode-separator-gap"),
    pytest.param("Bearer\u00a0s3cr3t", "Bearer [REDACTED]", id="nbsp-after-scheme"),
    pytest.param(
        'detail: {\\"api_key\\": \\"sk-abc\\"}',
        'detail: {\\"api_key=[REDACTED]',
        id="escaped-json-body",
    ),
]


@pytest.mark.parametrize(("text", "expected"), REDACTION_CASES)
def test_credentials_in_text_are_redacted(text: str, expected: str) -> None:
    assert redact_credentials(text) == expected


def test_credentials_in_nested_text_are_redacted() -> None:
    assert redact_credentials_deep({"detail": ["password: a,b"]}) == {
        "detail": ["password=[REDACTED]"]
    }


@pytest.mark.parametrize(
    "prose",
    [
        "Invalid credentials: check BAND_API_KEY, then retry",
        "Could not read cookies: permission denied",
    ],
)
def test_credential_words_in_prose_stay_readable(prose: str) -> None:
    assert redact_credentials(prose) == prose


@pytest.mark.parametrize(
    "field",
    [
        "tokenValue",
        "auth",
        "OpenAIAPIKey",
        "passwd",
        "api_key_value",
        "refresh_tokens",
        "api key value",
    ],
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
