"""What optional features a deployment serves.

A Band platform is not one platform: the SaaS node, an on-prem node whose
licence grants room files and an on-prem node whose licence does not all speak
the same API, and only the last one 404s the file endpoints. Nothing in the
SDK can know which it is talking to, so it asks — the platform publishes the
answer on ``GET /api/v1/agent/me`` under the same ``ff_*`` keys the web app and
JAM read from their own boot payload.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from band.core.types import Capability

# The flag key that gates each SDK capability, typed once. A second spelling
# anywhere else would fail silently as "the platform turned this off".
PLATFORM_CAPABILITY_FLAGS: dict[Capability, str] = {
    Capability.FILES: "ff_file_transfer",
}


def capabilities_the_platform_refuses(
    feature_flags: Mapping[str, Any] | None,
) -> frozenset[Capability]:
    """Capabilities this deployment says it does not serve.

    Only an explicit ``false`` refuses. A platform old enough to answer
    nothing, or to have never heard of the capability, is not saying no — and
    it may well serve the endpoints, so treating silence as a refusal would
    take working tools away from a deployment that has them.
    """
    if not feature_flags:
        return frozenset()

    return frozenset(
        capability
        for capability, flag in PLATFORM_CAPABILITY_FLAGS.items()
        if feature_flags.get(flag) is False
    )
