"""How a host's settings combine in an env-backed adapter config."""

from __future__ import annotations

import pytest
import yaml
from pydantic_settings import SettingsConfigDict

from band.core.adapterconfig import EnvAdapterConfig


class SampleConfig(EnvAdapterConfig):
    model_config = SettingsConfigDict(env_prefix="SAMPLE_")

    model: str | None = None
    timeout_s: float = 30.0
    tags: tuple[str, ...] = ()


def test_explicit_settings_beat_the_environment_which_beats_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment sets env vars; the host's YAML overrides some; a field
    neither sets keeps its default, and an empty variable counts as unset."""
    monkeypatch.setenv("SAMPLE_MODEL", "opus")
    monkeypatch.setenv("SAMPLE_TIMEOUT_S", "45")
    monkeypatch.setenv("SAMPLE_TAGS", "")
    host_yaml = yaml.safe_load("timeout_s: 5\n")

    config = SampleConfig.model_validate(host_yaml)

    assert config == SampleConfig(model="opus", timeout_s=5, tags=())
    assert SampleConfig.model_validate_json(config.model_dump_json()) == config


def test_a_misspelt_yaml_key_is_refused_and_a_loaded_config_stays_put() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        SampleConfig.model_validate(yaml.safe_load("timeout_seconds: 5\n"))

    config = SampleConfig.model_validate(yaml.safe_load("timeout_s: 5\n"))
    with pytest.raises(ValueError, match="frozen"):
        config.timeout_s = 1  # type: ignore[misc]
