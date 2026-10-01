"""Every adapter's config is plain data a host can keep in YAML or JSON.

Runs against each registered adapter's non-default config (``custom_kwargs``),
so a field that only holds a live object, or a subclass that loosens the
shared base, fails here.
"""

from __future__ import annotations

from typing import Any

import pytest

from band.core.adapterconfig import BaseAdapterConfig
from tests.framework_configs.adapters import AdapterConfig


def _custom_config(adapter_config: AdapterConfig) -> BaseAdapterConfig:
    config = adapter_config.custom_kwargs.get("config")
    assert isinstance(config, BaseAdapterConfig), (
        f"{adapter_config.display_name}: register a non-default config as "
        "custom_kwargs['config'] in tests/framework_configs/adapters.py"
    )
    return config


def test_the_config_survives_a_round_trip_through_json(
    adapter_config: AdapterConfig,
) -> None:
    config = _custom_config(adapter_config)

    assert type(config).model_validate_json(config.model_dump_json()) == config


def test_the_config_refuses_a_setting_it_does_not_have(
    adapter_config: AdapterConfig,
) -> None:
    config = _custom_config(adapter_config)
    data: dict[str, Any] = {**config.model_dump(), "not_a_setting": True}

    with pytest.raises(ValueError, match="not_a_setting"):
        type(config).model_validate(data)
