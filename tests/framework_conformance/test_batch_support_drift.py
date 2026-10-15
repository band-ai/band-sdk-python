"""An adapter declares batch support exactly when it has a batch probe."""

from __future__ import annotations

from tests.framework_configs.adapters import ADAPTER_CONFIGS
from tests.framework_conformance.batchprobes.acp import ADAPTERS as ACP_ADAPTERS
from tests.framework_conformance.batchprobes.registry import BATCH_PROBES


def declaring_adapters() -> set[str]:
    """Every adapter in the conformance matrix, plus the ACP family it excludes."""
    factories = {cfg.framework_id: cfg.adapter_factory for cfg in ADAPTER_CONFIGS}
    factories.update(ACP_ADAPTERS)
    return {
        framework_id
        for framework_id, build in factories.items()
        if build().supports_batch_input
    }


class TestBatchSupportDrift:
    def test_every_adapter_declaring_batch_support_has_a_probe(self) -> None:
        assert declaring_adapters() - set(BATCH_PROBES) == set()

    def test_every_probe_belongs_to_an_adapter_that_declares_support(self) -> None:
        assert set(BATCH_PROBES) - declaring_adapters() == set()
