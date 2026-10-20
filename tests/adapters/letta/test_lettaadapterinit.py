from __future__ import annotations

from band.adapters.letta import (
    LettaAdapter,
    LettaAdapterConfig,
    LettaMCPConfig,
)


class TestLettaAdapterInit:
    # Default values are not asserted here: that would only restate the
    # dataclass definition, and the frozen-config conformance suite already
    # pins ``LettaAdapter().config == LettaAdapterConfig()``.

    def test_custom_config_reaches_adapter(self) -> None:
        config = LettaAdapterConfig(
            base_url="http://custom:8283",
            provider_key="sk-test",
            mode="shared",
            mcp=LettaMCPConfig(mode="external", server_url="http://mcp:9000/sse"),
        )
        adapter = LettaAdapter(config=config)
        assert adapter.config is config

    def test_no_leaked_adapter_config_env_vars(
        self, assert_no_leaked_adapter_config_env: None
    ) -> None:
        """Requesting the fixture is the assertion — see its docstring."""
