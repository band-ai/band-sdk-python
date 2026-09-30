"""Model and reasoning-effort selection, checked against what a harness offers.

Every adapter speaks this vocabulary: ``SimpleAdapter.model_selection`` is what
it is configured to use, ``SimpleAdapter.list_models()`` is what its harness
advertises, and ``check_model_selection`` is the one rule deciding whether the
two agree. Harnesses report their own catalogs; nothing here hardcodes a model
or effort name.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from band.core.exceptions import BandConfigError
from band.core.validation import listing


class ModelSetting(StrEnum):
    """A selectable model setting, valued as its ``ModelSelection`` field.

    Declared in apply order: the model decides which efforts exist.
    """

    MODEL = "model"
    REASONING_EFFORT = "reasoning_effort"


class ModelSelection(BaseModel):
    """The model and reasoning effort an adapter is configured to use.

    ``None`` leaves the choice to the harness.
    """

    model_config = ConfigDict(frozen=True)

    model: str | None = None
    reasoning_effort: str | None = None

    @classmethod
    def only(cls, setting: ModelSetting, value: str) -> ModelSelection:
        """A selection of ``value`` for ``setting`` alone."""
        return cls.model_validate({setting: value})

    def value_of(self, setting: ModelSetting) -> str | None:
        return getattr(self, setting)

    @property
    def is_empty(self) -> bool:
        return self.model is None and self.reasoning_effort is None


class ModelChoice(BaseModel):
    """One model a harness advertises.

    ``efforts`` is ``None`` when the harness does not report them for this
    model (the effort goes unchecked), and ``()`` when the model offers none.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    label: str | None = None
    efforts: tuple[str, ...] | None = None
    default_effort: str | None = None


class ModelCatalog(BaseModel):
    """The models a harness advertises, and the one it currently uses."""

    model_config = ConfigDict(frozen=True)

    models: tuple[ModelChoice, ...]
    current_model: str | None = None

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(choice.id for choice in self.models)

    def choice(self, model_id: str) -> ModelChoice | None:
        return next((choice for choice in self.models if choice.id == model_id), None)


class ModelSelectionError(BandConfigError):
    """A configured model or effort is not what the harness advertises."""

    def __init__(
        self,
        message: str,
        *,
        setting: ModelSetting,
        value: str,
        advertised: tuple[str, ...],
    ) -> None:
        super().__init__(message)
        self.setting = setting
        self.value = value
        self.advertised = advertised


def check_model_selection(
    selection: ModelSelection, catalog: ModelCatalog | None
) -> None:
    """Raise ``ModelSelectionError`` when ``catalog`` rejects ``selection``.

    A ``None`` catalog means the harness advertises nothing, so nothing is
    checked. The effort is checked against the selected model, or the
    catalog's current model when none is selected.
    """
    if catalog is None:
        return
    if selection.model is not None:
        _check_model(selection.model, catalog)
    if selection.reasoning_effort is not None:
        _check_effort(
            selection.reasoning_effort,
            model_id=selection.model or catalog.current_model,
            catalog=catalog,
        )


def _check_model(model_id: str, catalog: ModelCatalog) -> None:
    if catalog.choice(model_id) is not None:
        return
    raise ModelSelectionError(
        f'model "{model_id}" is not advertised; available: '
        f"{listing(catalog.model_ids)}",
        setting=ModelSetting.MODEL,
        value=model_id,
        advertised=catalog.model_ids,
    )


def _check_effort(effort: str, *, model_id: str | None, catalog: ModelCatalog) -> None:
    choice = catalog.choice(model_id) if model_id is not None else None
    if choice is None or choice.efforts is None or effort in choice.efforts:
        return
    message = (
        f'reasoning effort "{effort}" is not advertised for model "{model_id}"; '
        f"available: {listing(choice.efforts)}"
        if choice.efforts
        else f'model "{model_id}" offers no reasoning effort'
    )
    raise ModelSelectionError(
        message,
        setting=ModelSetting.REASONING_EFFORT,
        value=effort,
        advertised=choice.efforts,
    )


__all__ = [
    "ModelCatalog",
    "ModelChoice",
    "ModelSelection",
    "ModelSelectionError",
    "ModelSetting",
    "check_model_selection",
]
