"""An adapter declares batch support exactly when it has a batch probe."""

from __future__ import annotations

import importlib.util

from tests.e2e.baseline.toolkit.adapters import declares_batch_input
from tests.framework_configs.adapters import ADAPTER_CONFIGS
from tests.framework_conformance.batchprobes.acp import ADAPTERS as ACP_ADAPTERS
from tests.framework_conformance.batchprobes.registry import BATCH_PROBES


def batch_declarations() -> dict[str, bool]:
    """Class declarations available in this lane, without starting runtimes."""
    declarations = {
        cfg.framework_id: declares_batch_input(cfg.framework_id)
        for cfg in ADAPTER_CONFIGS
    }
    if importlib.util.find_spec("acp") is not None:
        from band.integrations.acp.client_adapter import (  # noqa: PLC0415 -- optional acp extra
            ACPClientAdapter,
        )

        declarations.update(
            {
                framework_id: declares_batch_input(framework_id)
                for framework_id in ACP_ADAPTERS
                if framework_id != "acp"
            }
        )
        declarations["acp"] = ACPClientAdapter.supports_batch_input
    return declarations


class TestBatchSupportDrift:
    def test_every_adapter_declaring_batch_support_has_a_probe(self) -> None:
        declaring = {
            name for name, supports in batch_declarations().items() if supports
        }
        assert declaring - set(BATCH_PROBES) == set()

    def test_every_probe_belongs_to_an_adapter_that_declares_support(self) -> None:
        declarations = batch_declarations()
        declaring = {name for name, supports in declarations.items() if supports}
        assert set(BATCH_PROBES) & declarations.keys() <= declaring
