"""Every adapter that runs a batch turn, by ``framework_id``.

To add an adapter: write its probe in a module beside this one, register it
here, then set ``supports_batch_input`` on the adapter. ``test_batch_support_drift``
fails when an adapter declares batch support without a probe registered.
"""

from __future__ import annotations

from collections.abc import Sequence

from tests.framework_conformance.batchprobes import (
    FINAL_TEXT,
    BatchProbe,
    Original,
    delivery_input,
    toolloop,
)

BATCH_PROBES: dict[str, BatchProbe] = {**toolloop.PROBES}


async def provider_received(framework_id: str, originals: Sequence[Original]) -> str:
    """Everything the provider received for one turn over ``originals``."""
    inp = await delivery_input(originals)
    return await BATCH_PROBES[framework_id].run(inp, FINAL_TEXT)
