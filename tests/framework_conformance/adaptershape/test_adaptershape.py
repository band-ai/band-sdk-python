from __future__ import annotations

import pytest

from tests.framework_conformance.adaptershape.kit import (
    ADAPTER_CONFIGS,
    ADAPTERS,
    CONFIG_BASES,
    SourceClass,
    _constructor_shape_errors,
)


class TestAdapterShape:
    @pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda c: c.label)
    def test_adapter_takes_its_config_then_keyword_only_arguments(
        self,
        adapter: SourceClass,
    ) -> None:
        assert _constructor_shape_errors(adapter) == []

    @pytest.mark.parametrize("config", ADAPTER_CONFIGS, ids=lambda c: c.label)
    def test_adapter_config_extends_the_shared_base(self, config: SourceClass) -> None:
        config_names = {c.node.name for c in ADAPTER_CONFIGS}
        assert config.base_names & (CONFIG_BASES | config_names), (
            f"{config.label} must subclass BaseAdapterConfig or EnvAdapterConfig"
        )

    def test_every_adapter_has_its_config_class(
        self,
    ) -> None:
        config_names = {c.node.name for c in ADAPTER_CONFIGS}
        missing = [
            adapter.label
            for adapter in ADAPTERS
            if f"{adapter.node.name}Config" not in config_names
        ]
        assert missing == []
