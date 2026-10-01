"""Credential-safe baseline settings diagnostics."""

from __future__ import annotations

from tests.e2e.baseline.settings import (
    Backends,
    BandCredentials,
    BaselineSettings,
    LLMCredentials,
)


def test_settings_repr_omits_credentials() -> None:
    marker = "fake-secret-for-repr-test"
    settings = BaselineSettings(
        credentials=BandCredentials(
            api_key=marker,
            api_key_user=marker,
            api_key_user_2=marker,
        ),
        llm_credentials=LLMCredentials(
            openai_api_key=marker,
            anthropic_api_key=marker,
            google_api_key=marker,
            gemini_api_key=marker,
        ),
        backends=Backends(
            letta_api_key=marker,
            cursor_api_key=marker,
            github_token=marker,
        ),
    )

    for value in (
        settings,
        settings.credentials,
        settings.llm_credentials,
        settings.backends,
    ):
        assert marker not in repr(value)
