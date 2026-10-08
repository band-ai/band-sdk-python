"""Which Band platform tools a Parlant agent is offered."""

from __future__ import annotations

from band.core.tool_filter import filter_tool_schemas
from band.core.types import AdapterFeatures, Capability
from band.integrations.parlant.bandtool import BandToolSpec
from band.integrations.parlant.bandtools import chat, contacts, files, tasks
from band.runtime.tools import get_band_tool_category

# Tool families offered only when their capability is negotiated; chat is
# always offered.
GATED_TOOLS: dict[Capability, tuple[BandToolSpec, ...]] = {
    Capability.CONTACTS: contacts.TOOLS,
    Capability.FILES: files.TOOLS,
    Capability.TASKS: tasks.TOOLS,
}


def band_tool_specs(features: AdapterFeatures | None) -> list[BandToolSpec]:
    """The Band tool specs *features* enables and selects.

    No features means every family, unfiltered.
    """
    offered = [
        chat.TOOLS,
        *(
            family
            for capability, family in GATED_TOOLS.items()
            if features is None or capability in features.capabilities
        ),
    ]
    specs = [spec for family in offered for spec in family]
    if features is None:
        return specs
    return filter_tool_schemas(
        specs,
        features,
        get_name=lambda spec: spec.name,
        get_category=lambda spec: get_band_tool_category(spec.name),
    )
