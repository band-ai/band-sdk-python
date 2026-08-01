"""Shared pydantic bases for frozen contracts and env settings."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict


class FrozenModel(BaseModel):
    """Immutable contract / value model (``ConfigDict(frozen=True)``)."""

    model_config = ConfigDict(frozen=True)


class BandSettings(BaseSettings):
    """Env settings with the SDK's mandated ignore / case / empty-env policy.

    Subclasses normally set only ``env_prefix`` (and rare extras such as
    ``populate_by_name``); pydantic merges ``model_config`` across bases.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )
