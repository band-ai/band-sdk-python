"""Every adapter that runs a batch turn, by ``framework_id``.

To add an adapter: write its probe in a module beside this one, register it
here, then set ``supports_batch_input`` on the adapter. ``test_batch_support_drift``
fails when an adapter declares batch support without a probe registered.
"""

from __future__ import annotations

from tests.framework_conformance.batchprobes import BatchProbe, agentloops, toolloop

BATCH_PROBES: dict[str, BatchProbe] = {**toolloop.PROBES, **agentloops.PROBES}
