"""CopilotACPAdapterConfig maps onto the base ACP adapter's transport, auth,
and system-context wiring.

CopilotACPAdapter is a thin specialization of ACPClientAdapter; runtime and
on_started behavior is covered by the generic ACP client suite
(tests/integrations/acp/).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from band.adapters.copilot_acp import (
    DEFAULT_COPILOT_COMMAND,
    CopilotACPAdapter,
    CopilotACPAdapterConfig,
)
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.integrations.acp.client_profiles import NoopACPClientProfile
from tests.integrations.acp.acp_toolkit.harness import (
    launch_for,
    room_holder,
)


class TestCopilotACPAdapterConstruction:
    def test_is_acp_client_adapter(self) -> None:
        assert issubclass(CopilotACPAdapter, ACPClientAdapter)

    @pytest.mark.asyncio
    async def test_launches_copilot_with_its_ambient_login_by_default(
        self, tmp_path: Path
    ) -> None:
        # No token and no env -> the CLI's ambient login (stored / gh / BYOK).
        adapter = CopilotACPAdapter(CopilotACPAdapterConfig(cwd=str(tmp_path)))

        launch = await launch_for(adapter)

        assert launch.command == DEFAULT_COPILOT_COMMAND
        assert launch.env is None

    @pytest.mark.asyncio
    async def test_cwd_becomes_a_room_workspace_root(self, tmp_path: Path) -> None:
        adapter = CopilotACPAdapter(CopilotACPAdapterConfig(cwd=str(tmp_path)))

        launch = await launch_for(adapter, "room-a")

        assert launch.cwd == str(tmp_path / "room-a")

    def test_cwd_and_a_workspace_resolver_are_exclusive(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="set either cwd or workspace_for_room"):
            CopilotACPAdapter(
                CopilotACPAdapterConfig(cwd=str(tmp_path)),
                workspace_for_room=lambda room_id: str(tmp_path / room_id),
            )

    @pytest.mark.parametrize(
        "setting",
        [{"host": "10.0.0.5", "port": 8080}, {"port": 8080}],
        ids=["host-and-port", "port"],
    )
    def test_tcp_config_is_rejected(self, setting: dict[str, object]) -> None:
        with pytest.raises(
            ValueError,
            match="TCP ACP transport cannot guarantee room process isolation",
        ):
            CopilotACPAdapterConfig.model_validate(setting)

    def test_no_profile_uses_default_noop(self) -> None:
        # Copilot speaks vanilla ACP; the base adapter leaves profile unset and the
        # collecting client the runtime builds falls back to the no-op profile.
        adapter = CopilotACPAdapter()
        assert adapter._profile is None
        client = adapter._build_runtime(room_holder(adapter), 1)._client_factory()
        assert isinstance(client._profile, NoopACPClientProfile)

    @pytest.mark.parametrize(
        ("config", "env"),
        [
            pytest.param(
                CopilotACPAdapterConfig(github_token="ghp_x"),
                {"GITHUB_TOKEN": "ghp_x"},
                id="token",
            ),
            # Any auth Copilot supports (COPILOT_GITHUB_TOKEN, BYOK keys) passes
            # through env.
            pytest.param(
                CopilotACPAdapterConfig(
                    env={"COPILOT_GITHUB_TOKEN": "tok", "OTHER": "x"}
                ),
                {"COPILOT_GITHUB_TOKEN": "tok", "OTHER": "x"},
                id="env",
            ),
            pytest.param(
                CopilotACPAdapterConfig(github_token="ghp_x", env={"GH_TOKEN": "gh"}),
                {"GH_TOKEN": "gh", "GITHUB_TOKEN": "ghp_x"},
                id="token-merged-into-env",
            ),
            pytest.param(
                CopilotACPAdapterConfig(
                    github_token="from-shortcut", env={"GITHUB_TOKEN": "from-env"}
                ),
                {"GITHUB_TOKEN": "from-env"},
                id="env-wins-over-token",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_auth_reaches_the_cli_environment(
        self, config: CopilotACPAdapterConfig, env: dict[str, str], tmp_path: Path
    ) -> None:
        adapter = CopilotACPAdapter(
            config, workspace_for_room=lambda room_id: str(tmp_path / room_id)
        )

        launch = await launch_for(adapter)

        assert launch.env == env

    def test_additional_tools_are_registered(self) -> None:
        class EchoInput(BaseModel):
            text: str

        def _echo(text: str) -> str:
            return text

        adapter = CopilotACPAdapter(additional_tools=[(EchoInput, _echo)])

        assert "echo" in adapter._own_tool_names
