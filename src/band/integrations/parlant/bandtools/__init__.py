"""Which Band platform tools a Parlant agent is offered."""

from __future__ import annotations

from band.core.tool_filter import filter_tool_schemas
from band.core.types import AdapterFeatures
from band.integrations.parlant.bandtool import BandToolSpec
from band.integrations.parlant.bandtools import chat, contacts, files, tasks
from band.runtime.tools import get_band_tool_category, withheld_tool_names

ALL_TOOLS: tuple[BandToolSpec, ...] = (
    *chat.TOOLS,
    *contacts.TOOLS,
    *files.TOOLS,
    *tasks.TOOLS,
)


def band_tool_specs(features: AdapterFeatures | None) -> list[BandToolSpec]:
    """The Band tool specs *features* enables and selects.

    No features means every tool, unfiltered.
    """
    if features is None:
        return list(ALL_TOOLS)
    withheld = withheld_tool_names(features.capabilities)
    return filter_tool_schemas(
        [spec for spec in ALL_TOOLS if spec.name not in withheld],
        features,
        get_name=lambda spec: spec.name,
        get_category=lambda spec: get_band_tool_category(spec.name),
    )
