"""The baseline's ``batch_input`` selector follows each adapter class's own declaration."""

from __future__ import annotations

from tests.e2e.baseline.toolkit.adapters import registered_ids, specs
from tests.framework_configs.adapters import ADAPTER_CONFIGS


class TestBatchInputSelector:
    def test_it_selects_exactly_the_adapters_whose_instance_declares_batch_input(
        self,
    ) -> None:
        constructible = {
            cfg.framework_id: cfg.adapter_factory().supports_batch_input
            for cfg in ADAPTER_CONFIGS
            if cfg.framework_id in registered_ids()
        }
        selected = {str(spec.id) for spec in specs(batch_input=True)}
        declaring = {adapter for adapter, declares in constructible.items() if declares}

        assert selected & constructible.keys() == declaring

    def test_the_two_sides_of_the_selector_partition_the_registry(self) -> None:
        batching = {spec.id for spec in specs(batch_input=True, include_pending=True)}
        singleton = {spec.id for spec in specs(batch_input=False, include_pending=True)}

        assert batching.isdisjoint(singleton)
        assert batching | singleton == registered_ids()
