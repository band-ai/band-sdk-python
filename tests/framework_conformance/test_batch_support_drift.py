"""An adapter declares batch support exactly when it has a batch probe."""

from __future__ import annotations

from tests.framework_configs.adapters import ADAPTER_CONFIGS
from tests.framework_conformance.batchprobes.registry import BATCH_PROBES


def declaring_adapters() -> set[str]:
    return {
        cfg.framework_id
        for cfg in ADAPTER_CONFIGS
        if cfg.adapter_factory().supports_batch_input
    }


class TestBatchSupportDrift:
    def test_every_adapter_declaring_batch_support_has_a_probe(self) -> None:
        assert declaring_adapters() - set(BATCH_PROBES) == set()

    def test_every_probe_belongs_to_an_adapter_that_declares_support(self) -> None:
        assert set(BATCH_PROBES) - declaring_adapters() == set()
