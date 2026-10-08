"""Which Band platform tools a Parlant agent is offered."""

from __future__ import annotations

from band.core.types import AdapterFeatures, Capability
from band.integrations.parlant.bandtool import BandToolSpec
from band.integrations.parlant.bandtools import chat, contacts, files, tasks

# Tool families offered only when their capability is negotiated; chat is
# always offered.
GATED_TOOLS: dict[Capability, tuple[BandToolSpec, ...]] = {
    Capability.CONTACTS: contacts.TOOLS,
    Capability.FILES: files.TOOLS,
    Capability.TASKS: tasks.TOOLS,
}


def band_tool_specs(features: AdapterFeatures | None) -> list[BandToolSpec]:
    """The Band tool specs *features* enables; no features means every family."""
    capabilities = features.capabilities if features else None
    specs = list(chat.TOOLS)
    for capability, family in GATED_TOOLS.items():
        if capabilities is None or capability in capabilities:
            specs.extend(family)
    return specs
