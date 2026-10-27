"""Receiver configuration loads explicit environment credentials."""

from __future__ import annotations

from types import ModuleType

import pytest
from pydantic import ValidationError

from tests.oneshot_server.kit import CREDENTIALS


def missing_fields(error: ValidationError) -> list[tuple[tuple[str | int, ...], str]]:
    return [(detail["loc"], detail["type"]) for detail in error.errors()]


class TestSettings:
    @pytest.mark.parametrize("name", CREDENTIALS)
    def test_each_credential_is_required(
        self,
        receiver: ModuleType,
        credentials: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
        name: str,
    ) -> None:
        monkeypatch.delenv(name)
        with pytest.raises(ValidationError) as failure:
            receiver.Settings()
        assert missing_fields(failure.value) == [((name.lower(),), "missing")]

    @pytest.mark.parametrize("name", CREDENTIALS)
    def test_empty_credential_counts_as_missing(
        self,
        receiver: ModuleType,
        credentials: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
        name: str,
    ) -> None:
        monkeypatch.setenv(name, "")
        with pytest.raises(ValidationError) as failure:
            receiver.Settings()
        assert missing_fields(failure.value) == [((name.lower(),), "missing")]

    def test_loads_credentials(
        self, receiver: ModuleType, credentials: dict[str, str]
    ) -> None:
        settings = receiver.Settings()
        assert (
            settings.band_agent_id,
            settings.band_api_key,
            settings.anthropic_api_key,
        ) == tuple(credentials.values())
