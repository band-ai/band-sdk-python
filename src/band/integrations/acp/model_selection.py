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
from band.core.validation import listing
from band.integrations.acp.session_config import (
    ACPConfigError,
    SessionConfigOption,
    SessionConfigSetter,
    apply_session_config_selections,
    flatten_select_options,
    select_ids,
    select_values,
    selects,
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

    def current_selection(self) -> ModelSelection:
        """What the session currently runs; no effort when it offers none."""
        return ModelSelection(
            model=self.model.current_value if self.model else None,
            reasoning_effort=self.effort.current_value if self.effort else None,
        )

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
        efforts: tuple[str, ...] | None = None
        default_effort: str | None = None
        if entry.value == current:
            efforts = select_values(self.effort) if self.effort else ()
            default_effort = self.effort.current_value if self.effort else None
        return ModelChoice(
            id=entry.value,
            label=entry.name,
            efforts=efforts,
            default_effort=default_effort,
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
    catalog: Callable[[], Sequence[SessionConfigOption]],
    selection: ModelSelection,
    locate: Callable[[Sequence[SessionConfigOption]], ACPModelOptions],
    set_option: SessionConfigSetter,
) -> None:
    """Select the model, then the effort, each against the session's catalog
    as it stands then.

    Choosing a model can add, remove, or replace the effort select, so the
    effort is located and checked only after the model has been applied.
    """
    for setting in ModelSetting:
        value = selection.value_of(setting)
        if value is None:
            continue
        await _apply_setting(
            session_id=session_id,
            config_options=tuple(catalog()),
            setting=setting,
            value=value,
            locate=locate,
            set_option=set_option,
        )


async def _apply_setting(
    *,
    session_id: str,
    config_options: tuple[SessionConfigOption, ...],
    setting: ModelSetting,
    value: str,
    locate: Callable[[Sequence[SessionConfigOption]], ACPModelOptions],
    set_option: SessionConfigSetter,
) -> None:
    located = locate(config_options)
    option = located.option_for(setting)
    option_id = option.id if option is not None else setting
    try:
        check_model_selection(
            ModelSelection.model_validate({setting: value}),
            located.model_catalog(),
        )
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
            message=(
                f"ACP session advertises no {setting} option; available: "
                f"{listing(select_ids(config_options))}."
            ),
        )
    await apply_session_config_selections(
        session_id=session_id,
        config_options=config_options,
        selections={option.id: value},
        set_option=set_option,
    )


def _select_in_category(
    options: Sequence[SessionConfigOption], category: str
) -> SessionConfigOptionSelect | None:
    return next(
        (option for option in selects(options) if option.category == category), None
    )


__all__ = [
    "MODEL_CATEGORY",
    "THOUGHT_LEVEL_CATEGORY",
    "ACPModelOptions",
    "apply_model_selection",
    "locate_model_options",
]
