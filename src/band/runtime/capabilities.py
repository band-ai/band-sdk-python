"""Capability <-> platform feature-flag negotiation.

Single source of truth mapping an SDK :class:`~band.core.types.Capability`
to the platform deployment flag that gates it, so a capability an adapter
requested but the connected deployment doesn't actually serve is dropped
before it ever reaches tool-schema building -- an absent capability, not a
tool that 404s on first call.
"""

from __future__ import annotations

import dataclasses
from enum import StrEnum

from band.core.types import AdapterFeatures, Capability


class FeatureFlag(StrEnum):
    """Keys the platform's ``AgentMe.feature_flags`` dict is known to use.

    A member is a plain ``str``, so it works directly as a dict key
    (``feature_flags.get(FeatureFlag.FILE_TRANSFER)``) against the
    Fern-generated ``dict[str, bool]`` -- this just names the keys this SDK
    actually reads, so the name can't drift between here and call sites.
    """

    FILE_TRANSFER = "ff_file_transfer"


# Capability -> the platform's `AgentMe.feature_flags` key that gates it.
# Extend here as more capabilities gain a platform-side deployment flag.
CAPABILITY_FEATURE_FLAGS: dict[Capability, FeatureFlag] = {
    Capability.FILES: FeatureFlag.FILE_TRANSFER,
}


def prune_unsupported(
    features: AdapterFeatures, feature_flags: dict[str, bool] | None
) -> AdapterFeatures:
    """Drop capabilities the connected deployment does not actually serve.

    ``feature_flags`` is the agent's own ``/me`` response. Every flag key
    the platform knows about is always present in that dict -- ``False``
    means the deployment doesn't serve it, and a key **missing entirely**
    means the platform predates it, which is equally unsupported. ``None``
    means the fetch itself never ran or failed: genuinely no information,
    so nothing is pruned in that case -- there is no basis to refuse.
    """
    if feature_flags is None:
        return features

    def supported(capability: Capability) -> bool:
        flag = CAPABILITY_FEATURE_FLAGS.get(capability)
        return flag is None or feature_flags.get(flag) is True

    pruned = frozenset(c for c in features.capabilities if supported(c))
    if pruned == features.capabilities:
        return features
    return dataclasses.replace(features, capabilities=pruned)


def with_hub_room_contacts(
    capabilities: frozenset[Capability], *, is_hub_room: bool
) -> frozenset[Capability]:
    """Force ``Capability.CONTACTS`` on for the hub-room execution path.

    The hub-room prompt instructs the LLM to call contact tools regardless of
    what an adapter's own capabilities negotiated, so every tool-schema
    call site that can be bound to the hub room applies this on top of its
    own resolved set. One definition shared by ``AgentTools.get_tool_schemas``
    and every adapter that separately re-resolves capabilities per turn
    (``AgnoAdapter``, ``langchain_tools.get_band_tools``).
    """
    if is_hub_room:
        return capabilities | {Capability.CONTACTS}
    return capabilities
