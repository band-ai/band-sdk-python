from __future__ import annotations

import pytest

from band.adapters.letta import (
    LettaAdapterConfig,
)


class TestConfigEnvSourcing:
    """provider_key env sourcing: LETTA_* names only, never bare vars."""

    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("API_KEY", "PROVIDER_KEY", "LETTA_API_KEY", "LETTA_PROVIDER_KEY"):
            monkeypatch.delenv(var, raising=False)

    def test_bare_env_vars_never_populate_provider_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A foreign secret in API_KEY/PROVIDER_KEY must not reach Letta."""
        monkeypatch.setenv("API_KEY", "foreign-secret")
        monkeypatch.setenv("PROVIDER_KEY", "foreign-secret")

        assert LettaAdapterConfig().provider_key is None

    def test_letta_api_key_env_populates_provider_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LETTA_API_KEY", "cloud-key")

        assert LettaAdapterConfig().provider_key == "cloud-key"

    def test_prefixed_field_name_env_populates_provider_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LETTA_PROVIDER_KEY", "prefixed-key")

        assert LettaAdapterConfig().provider_key == "prefixed-key"

    def test_explicit_kwarg_wins_over_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LETTA_API_KEY", "env-key")

        assert LettaAdapterConfig(provider_key="kwarg-key").provider_key == "kwarg-key"

    def test_prefixed_env_fills_a_field_and_an_explicit_value_beats_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LETTA_MODEL", "openai/env-model")

        assert LettaAdapterConfig().model == "openai/env-model"
        assert LettaAdapterConfig(model="openai/explicit").model == "openai/explicit"
