"""Capability <-> platform feature-flag negotiation.

Single source of truth mapping an SDK :class:`~band.core.types.Capability`
to the platform deployment flag that gates it, so a capability an adapter
requested but the connected deployment doesn't actually serve is dropped
before it ever reaches tool-schema building -- an absent capability, not a
tool that 404s on first call.
"""

from __future__ import annotations

import dataclasses

from band.core.types import AdapterFeatures, Capability

# Capability -> the platform's `AgentMe.feature_flags` key that gates it.
# Extend here as more capabilities gain a platform-side deployment flag.
CAPABILITY_FEATURE_FLAGS: dict[Capability, str] = {
    Capability.FILES: "ff_file_transfer",
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
