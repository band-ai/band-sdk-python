"""Apply a typed ``ModelSelection`` to an ACP session's live config catalog."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from acp.schema import SessionConfigOptionSelect, SessionConfigSelectOption

from band.core.model_catalog import (
    ModelCatalog,
    ModelChoice,
    ModelSelection,
    ModelSelectionError,
    ModelSetting,
    check_model_selection,
)
from band.integrations.acp.session_config import (
    ACPConfigError,
    SessionConfigOption,
    SessionConfigSetter,
    apply_session_config_selections,
    flatten_select_options,
    select_values,
)

# Category ids the ACP spec reserves for these selectors.
MODEL_CATEGORY = "model"
THOUGHT_LEVEL_CATEGORY = "thought_level"


@dataclass(frozen=True)
class ACPModelOptions:
    """The selects a session uses for its model and reasoning effort."""

    model: SessionConfigOptionSelect | None
    effort: SessionConfigOptionSelect | None

    def option_for(self, setting: ModelSetting) -> SessionConfigOptionSelect | None:
        match setting:
            case ModelSetting.MODEL:
                return self.model
            case ModelSetting.REASONING_EFFORT:
                return self.effort

    def model_catalog(self) -> ModelCatalog | None:
        """The advertised models; efforts are known only for the current one."""
        if self.model is None:
            return None
        return ModelCatalog(
            models=tuple(
                self._choice(entry, current=self.model.current_value)
                for entry in flatten_select_options(self.model.options)
            ),
            current_model=self.model.current_value,
        )

    def _choice(self, entry: SessionConfigSelectOption, *, current: str) -> ModelChoice:
        if entry.value != current:
            return ModelChoice(id=entry.value, label=entry.name)
        if self.effort is None:
            return ModelChoice(id=entry.value, label=entry.name, efforts=())
        return ModelChoice(
            id=entry.value,
            label=entry.name,
            efforts=select_values(self.effort),
            default_effort=self.effort.current_value,
        )


def locate_model_options(options: Sequence[SessionConfigOption]) -> ACPModelOptions:
    """Find the model and effort selects by their spec-reserved categories."""
    return ACPModelOptions(
        model=_select_in_category(options, MODEL_CATEGORY),
        effort=_select_in_category(options, THOUGHT_LEVEL_CATEGORY),
    )


async def apply_model_selection(
    *,
    session_id: str,
    config_options: Sequence[SessionConfigOption],
    selection: ModelSelection,
    locate: Callable[[Sequence[SessionConfigOption]], ACPModelOptions],
    set_option: SessionConfigSetter,
) -> tuple[SessionConfigOption, ...]:
    """Select the model, then the effort from the catalog that model returns.

    Choosing a model can add, remove, or replace the effort select, so the
    effort is located and checked only after the model has been applied.
    Returns the catalog after the last change.
    """
    catalog = tuple(config_options)
    for setting, value in (
        (ModelSetting.MODEL, selection.model),
        (ModelSetting.REASONING_EFFORT, selection.reasoning_effort),
    ):
        if value is None:
            continue
        catalog = await _apply_setting(
            session_id=session_id,
            catalog=catalog,
            setting=setting,
            value=value,
            locate=locate,
            set_option=set_option,
        )
    return catalog


async def _apply_setting(
    *,
    session_id: str,
    catalog: tuple[SessionConfigOption, ...],
    setting: ModelSetting,
    value: str,
    locate: Callable[[Sequence[SessionConfigOption]], ACPModelOptions],
    set_option: SessionConfigSetter,
) -> tuple[SessionConfigOption, ...]:
    located = locate(catalog)
    option = located.option_for(setting)
    option_id = option.id if option is not None else setting
    try:
        check_model_selection(_selection_of(setting, value), located.model_catalog())
    except ModelSelectionError as error:
        raise ACPConfigError(
            session_id=session_id,
            option_id=option_id,
            selected_value=value,
            message=str(error),
        ) from error
    if option is None:
        raise ACPConfigError(
            session_id=session_id,
            option_id=option_id,
            selected_value=value,
            message=f"ACP session advertises no {setting} option.",
        )
    return await apply_session_config_selections(
        session_id=session_id,
        config_options=catalog,
        selections={option.id: value},
        set_option=set_option,
    )


def _selection_of(setting: ModelSetting, value: str) -> ModelSelection:
    match setting:
        case ModelSetting.MODEL:
            return ModelSelection(model=value)
        case ModelSetting.REASONING_EFFORT:
            return ModelSelection(reasoning_effort=value)


def _select_in_category(
    options: Sequence[SessionConfigOption], category: str
) -> SessionConfigOptionSelect | None:
    return next(
        (
            option
            for option in options
            if isinstance(option, SessionConfigOptionSelect)
            and option.category == category
        ),
        None,
    )


__all__ = [
    "MODEL_CATEGORY",
    "THOUGHT_LEVEL_CATEGORY",
    "ACPModelOptions",
    "apply_model_selection",
    "locate_model_options",
]
