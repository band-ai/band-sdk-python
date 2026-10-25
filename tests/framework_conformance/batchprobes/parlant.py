"""Batch-input probe for the parlant adapter; see ``batchprobes``.

Parlant's own engine composes the answer from the session's events, so the
probe reads those events back as the engine's input; the adapter relays the
engine's answer to the turn's senders.
"""

from __future__ import annotations

import pytest

from band.adapters.parlant import ParlantAdapter, ParlantAdapterConfig
from band.core.types import AgentInput
from tests.framework_conformance.batchprobes import BatchProbe, deliver

# The peer answers at once; a bound keeps a missing answer from waiting out
# the production budget.
PROBE_CONFIG = ParlantAdapterConfig(response_timeout=5.0, response_poll=0.1)


async def run_parlant(inp: AgentInput, final_text: str) -> str:
    pytest.importorskip("parlant.sdk")
    from tests.framework_conformance.batchprobes.parlantpeer import (  # noqa: PLC0415 -- imports parlant, absent from some lane venvs
        ParlantPeer,
    )

    async with ParlantPeer(reply=final_text) as peer:
        adapter = ParlantAdapter(
            PROBE_CONFIG, server=peer.server, parlant_agent=peer.agent
        )
        await adapter.on_started("Agent", "A test agent")
        try:
            await deliver(adapter, inp)
            return peer.engine_input
        finally:
            await adapter.on_cleanup(inp.room_id)


PROBES: dict[str, BatchProbe] = {"parlant": BatchProbe(run=run_parlant, relays=True)}
