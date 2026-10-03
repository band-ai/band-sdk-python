"""Tests for KiroACPAdapter's presets over ACPClientAdapter.

Runtime behavior is covered by the generic ACP client suite (tests/integrations/acp/).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.kiro_acp import (
    DEFAULT_KIRO_COMMAND,
    KiroACPAdapter,
    KiroACPAdapterConfig,
)
from band.integrations.acp.client_profiles import KiroACPClientProfile
from tests.integrations.acp.acp_toolkit.harness import launch_for


class TestKiroACPAdapterConstruction:
    @pytest.mark.asyncio
    async def test_launches_kiro_with_its_ambient_login_by_default(
        self, tmp_path: Path
    ) -> None:
        adapter = KiroACPAdapter(KiroACPAdapterConfig(cwd=str(tmp_path)))

        launch = await launch_for(adapter)

        assert launch.command == DEFAULT_KIRO_COMMAND
        assert launch.env is None

    @pytest.mark.asyncio
    async def test_config_command_and_env_reach_the_launched_agent(
        self, tmp_path: Path
    ) -> None:
        adapter = KiroACPAdapter(
            KiroACPAdapterConfig(
                command=("kiro-cli", "acp", "--agent-engine", "v3"),
                env={"KIRO_API_KEY": "tok", "KIRO_HOME": str(tmp_path / "home")},
                cwd=str(tmp_path),
            )
        )

        launch = await launch_for(adapter)

        assert launch.command == ("kiro-cli", "acp", "--agent-engine", "v3")
        assert launch.env == {
            "KIRO_API_KEY": "tok",
            "KIRO_HOME": str(tmp_path / "home"),
        }

    def test_runtime_client_carries_the_kiro_profile(self) -> None:
        client = KiroACPAdapter()._build_runtime()._client_factory()

        assert isinstance(client._profile, KiroACPClientProfile)
