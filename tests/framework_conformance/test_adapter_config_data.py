"""Every adapter's config is plain data a host can keep in YAML or JSON.

Runs against each registered adapter's non-default config (``custom_kwargs``),
so a field that only holds a live object, or a subclass that loosens the
shared base, fails here.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, get_args

import pytest
from pydantic import BaseModel

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


CREDENTIAL_FIELD = re.compile(r"_(key|token|secret)$")


def _exposed_credentials(model: type[BaseModel]) -> Iterator[str]:
    """``Model.field`` for each credential shown by repr, nested configs included."""
    for name, field in model.model_fields.items():
        if CREDENTIAL_FIELD.search(name) and field.repr:
            yield f"{model.__name__}.{name}"
        for nested in _models_in(field.annotation):
            yield from _exposed_credentials(nested)


def _models_in(annotation: object) -> Iterator[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
    for arg in get_args(annotation):
        yield from _models_in(arg)


def test_the_config_repr_never_shows_a_credential(
    adapter_config: AdapterConfig,
) -> None:
    config = _custom_config(adapter_config)

    assert list(_exposed_credentials(type(config))) == [], (
        "configs reach logs and tracebacks; declare these with repr=False"
    )
