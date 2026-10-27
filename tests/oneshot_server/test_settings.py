"""Receiver configuration loads explicit environment credentials."""

from __future__ import annotations

from types import ModuleType

import pytest
from pydantic import ValidationError

CREDENTIALS = {
    "BAND_AGENT_ID": "agent-1",
    "BAND_API_KEY": "key-1",
    "ANTHROPIC_API_KEY": "anthropic-1",
}


class TestSettings:
    @pytest.mark.parametrize("name", CREDENTIALS)
    def test_each_credential_is_required(
        self, receiver: ModuleType, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        for setting, credential in CREDENTIALS.items():
            monkeypatch.setenv(setting, credential)
        monkeypatch.delenv(name)
        with pytest.raises(ValidationError):
            receiver.Settings()

    @pytest.mark.parametrize("name", CREDENTIALS)
    def test_empty_credential_counts_as_missing(
        self, receiver: ModuleType, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        for setting, credential in CREDENTIALS.items():
            monkeypatch.setenv(setting, credential)
        monkeypatch.setenv(name, "")
        with pytest.raises(ValidationError):
            receiver.Settings()

    def test_loads_credentials(
        self, receiver: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for setting, credential in CREDENTIALS.items():
            monkeypatch.setenv(setting, credential)
        settings = receiver.Settings()
        assert (
            settings.band_agent_id,
            settings.band_api_key,
            settings.anthropic_api_key,
        ) == tuple(CREDENTIALS.values())
