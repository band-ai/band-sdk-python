"""The one rule deciding whether a harness accepts a model/effort selection."""

from __future__ import annotations

import pytest

from band.core.model_catalog import (
    ModelCatalog,
    ModelChoice,
    ModelSelection,
    ModelSelectionError,
    ModelSetting,
    check_model_selection,
)

CATALOG = ModelCatalog(
    models=(
        ModelChoice(id="sonnet", efforts=("low", "high", "max")),
        ModelChoice(id="haiku", efforts=()),
        ModelChoice(id="gpt", efforts=None),
    ),
    current_model="sonnet",
)


def rejection(selection: ModelSelection) -> ModelSelectionError:
    with pytest.raises(ModelSelectionError) as caught:
        check_model_selection(selection, CATALOG)
    return caught.value


@pytest.mark.parametrize(
    "selection",
    [
        ModelSelection(model="haiku"),
        ModelSelection(model="sonnet", reasoning_effort="max"),
        ModelSelection(reasoning_effort="low"),  # against the current model
        ModelSelection(model="gpt", reasoning_effort="anything"),  # not reported
    ],
)
def test_advertised_selections_pass(selection: ModelSelection) -> None:
    check_model_selection(selection, CATALOG)


def test_anything_passes_when_the_harness_advertises_no_catalog() -> None:
    check_model_selection(ModelSelection(model="x", reasoning_effort="y"), None)


def test_unknown_model_names_what_is_advertised() -> None:
    error = rejection(ModelSelection(model="gpt-9"))

    assert (error.setting, error.value) == (ModelSetting.MODEL, "gpt-9")
    assert (
        str(error) == 'model "gpt-9" is not advertised; available: sonnet, haiku, gpt'
    )


def test_effort_is_checked_against_the_selected_model_not_the_current_one() -> None:
    error = rejection(ModelSelection(model="haiku", reasoning_effort="high"))

    assert (error.setting, error.value) == (ModelSetting.REASONING_EFFORT, "high")
    assert str(error) == 'model "haiku" offers no reasoning effort'


def test_unadvertised_effort_names_the_models_efforts() -> None:
    error = rejection(ModelSelection(reasoning_effort="xhigh"))

    assert str(error) == (
        'reasoning effort "xhigh" is not advertised for model "sonnet"; '
        "available: low, high, max"
    )
