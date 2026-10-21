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
    acp,
    agentloops,
    claudesdk,
    codex,
    copilotsdk,
    crewai,
    delivery_input,
    gemini,
    googleadk,
    letta,
    nooa,
    opencode,
    parlant,
    toolloop,
)

BATCH_PROBES: dict[str, BatchProbe] = {
    **acp.PROBES,
    **agentloops.PROBES,
    **claudesdk.PROBES,
    **codex.PROBES,
    **copilotsdk.PROBES,
    **crewai.PROBES,
    **gemini.PROBES,
    **googleadk.PROBES,
    **letta.PROBES,
    **nooa.PROBES,
    **opencode.PROBES,
    **parlant.PROBES,
    **toolloop.PROBES,
}


async def provider_received(framework_id: str, originals: Sequence[Original]) -> str:
    """Everything the provider received for one turn over ``originals``."""
    inp = await delivery_input(originals)
    return await BATCH_PROBES[framework_id].run(inp, FINAL_TEXT)
