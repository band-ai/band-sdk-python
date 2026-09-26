"""Tests for KiroACPAdapter's presets over ACPClientAdapter.

Runtime behavior is covered by the generic ACP client suite (tests/integrations/acp/).
"""

from __future__ import annotations

from band.adapters.kiro_acp import (
    DEFAULT_KIRO_COMMAND,
    KiroACPAdapter,
    KiroACPAdapterConfig,
)
from band.integrations.acp.client_profiles import KiroACPClientProfile


class TestKiroACPAdapterConstruction:
    def test_defaults_to_stdio_kiro_command_and_ambient_login(self) -> None:
        adapter = KiroACPAdapter()

        assert adapter._command == list(DEFAULT_KIRO_COMMAND)
        assert adapter._env is None

    def test_config_command_and_env_are_forwarded(self) -> None:
        adapter = KiroACPAdapter(
            KiroACPAdapterConfig(
                command=("kiro-cli", "acp", "--agent-engine", "v3"),
                env={"KIRO_API_KEY": "tok", "KIRO_HOME": "/tmp/kiro-home"},
            )
        )

        assert adapter._command == ["kiro-cli", "acp", "--agent-engine", "v3"]
        assert adapter._env == {"KIRO_API_KEY": "tok", "KIRO_HOME": "/tmp/kiro-home"}

    def test_runtime_client_carries_the_kiro_profile(self) -> None:
        client = KiroACPAdapter()._build_runtime()._client_factory()

        assert isinstance(client._profile, KiroACPClientProfile)
