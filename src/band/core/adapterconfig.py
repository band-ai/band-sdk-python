"""The one configuration convention every adapter follows.

Every adapter is built the same way::

    XAdapter(
        config: XAdapterConfig | None = None,  # plain settings data
        *,
        history_converter=...,  # when the adapter takes one
        additional_tools=...,  # when the adapter takes them
        <live objects>=...,  # clients, graphs, factories, callbacks
        **features: Unpack[FeatureKwargs],
    )

``XAdapterConfig`` holds values a host could write down (model names, prompts,
timeouts, modes) and subclasses :class:`BaseAdapterConfig`, or
:class:`EnvAdapterConfig` when its fields may also come from environment
variables. Live objects stay keyword-only constructor arguments, so a config can
always be loaded from YAML or JSON with ``XAdapterConfig.model_validate(data)``.
``tests/framework_conformance/test_adapter_shape.py`` enforces this shape.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseAdapterConfig(BaseModel):
    """Base for every adapter config: immutable once built, and an unknown
    field fails construction instead of silently vanishing, since configs are
    typically built from many explicit keyword arguments."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class EnvAdapterConfig(BaseAdapterConfig, BaseSettings):
    """An adapter config whose fields may also be read from environment
    variables named by the subclass's ``env_prefix``; an explicit value always
    wins over the environment."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        frozen=True,
        extra="forbid",
        case_sensitive=False,
        env_ignore_empty=True,
    )
