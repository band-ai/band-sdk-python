"""Conformance test that the text adapters advertise came from the master models.

``runtime/tools.py`` is the single source of truth for what an LLM reads about
a platform tool: the input model's docstring is the tool description and each
``Field(description=...)`` is an argument description. An adapter can lose that
in two ways, and both have shipped: retyping the text locally (where it drifts)
or handing the framework a schema shape that carries no argument text at all.

An adapter opts in by setting ``AdapterConfig.advertised_arg_text`` to a probe
returning what it really advertises. Adapters that pass the master schema
through untouched leave it unset — see the field's docstring.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from band.runtime.tools import TOOL_MODELS
from tests.framework_configs.adapters import ADAPTER_CONFIGS, AdapterConfig

# Probes are wired per lane (CrewAI only builds tools where crewai is
# installed), so an empty set means "no probeable adapter in this lane", not a
# silent hole — configs_with_probe is asserted non-empty per parametrized case.
PROBED_CONFIGS = [cfg for cfg in ADAPTER_CONFIGS if cfg.advertised_arg_text is not None]


def master_arg_text(tool_names: Iterable[str]) -> dict[str, dict[str, str | None]]:
    """The argument text the master models define for *tool_names*."""
    return {
        name: {
            arg: field.description
            for arg, field in TOOL_MODELS[name].model_fields.items()
        }
        for name in tool_names
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("config", PROBED_CONFIGS, ids=lambda cfg: cfg.framework_id)
async def test_advertised_arg_text_matches_master(config: AdapterConfig) -> None:
    advertised = await config.advertised_arg_text()

    unknown = set(advertised) - set(TOOL_MODELS)
    assert not unknown, (
        f"{config.display_name} advertises tools with no master model: {unknown}. "
        "A probe must report platform tools only."
    )
    assert advertised, f"{config.display_name} probe advertised no tools"
    # Expected keys come from the master, so a dropped or renamed argument
    # fails here too — not just changed wording.
    assert advertised == master_arg_text(advertised)


def test_some_adapter_is_probed() -> None:
    """A lane where every probe silently vanished would pass vacuously."""
    assert PROBED_CONFIGS, (
        "No adapter in this lane wired advertised_arg_text. Either a probe was "
        "dropped, or a config failed to build and was swallowed — check that "
        "the adapters this lane installs are registered in ADAPTER_CONFIGS."
    )
