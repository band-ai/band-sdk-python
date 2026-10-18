"""Project CLI-advertised model aliases without inferring provider or selection."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from band.core.exceptions import BandConfigError
from band.core.model_catalog import ModelCatalog, ModelChoice


class _Model(BaseModel):
    value: str
    display_name: str | None = Field(default=None, alias="displayName")
    efforts: tuple[str, ...] | None = Field(default=None, alias="supportedEffortLevels")
    supports_effort: bool | None = Field(default=None, alias="supportsEffort")


class _Initialization(BaseModel):
    models: tuple[_Model, ...]


def catalog_from_server_info(info: dict[str, Any]) -> ModelCatalog:
    """Reject missing or malformed inventory rather than returning an empty one."""
    try:
        metadata = _Initialization.model_validate(info)
    except ValidationError:
        raise BandConfigError(
            "Claude initialization did not provide a valid model catalog. "
            "Check the configured Claude Code version and model settings."
        ) from None
    return ModelCatalog(models=tuple(_choice(model) for model in metadata.models))


def _choice(model: _Model) -> ModelChoice:
    efforts = model.efforts
    if efforts is None and model.supports_effort is False:
        efforts = ()
    return ModelChoice(
        id=model.value,
        label=model.display_name or model.value,
        efforts=efforts,
        is_default=model.value == "default",
    )
