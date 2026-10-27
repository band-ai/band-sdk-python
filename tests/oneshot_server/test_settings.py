"""Receiver configuration loads explicit environment credentials."""

from __future__ import annotations

from types import ModuleType

import pytest
from pydantic import ValidationError


class TestSettings:
    def test_requires_credentials(
        self, receiver: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BAND_AGENT_ID", raising=False)
        monkeypatch.delenv("BAND_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValidationError):
            receiver.Settings()

    def test_loads_credentials(
        self, receiver: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BAND_AGENT_ID", "agent-1")
        monkeypatch.setenv("BAND_API_KEY", "key-1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-1")
        settings = receiver.Settings()
        assert (
            settings.band_agent_id,
            settings.band_api_key,
            settings.anthropic_api_key,
        ) == ("agent-1", "key-1", "anthropic-1")
