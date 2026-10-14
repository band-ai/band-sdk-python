"""How the Cursor ACP adapter launches ``agent acp``."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.cursor_acp import (
    DEFAULT_CURSOR_ACP_COMMAND,
    CursorACPAdapter,
    CursorACPAdapterConfig,
)
from tests.adapters.cursor_acp.kit import cursor_in
from tests.integrations.acp.acp_toolkit.harness import launch_for


class TestCursorACPAdapterLaunch:
    @pytest.mark.asyncio
    async def test_launches_agent_acp_without_authenticate(
        self, tmp_path: Path
    ) -> None:
        adapter = cursor_in(tmp_path)

        launch = await launch_for(adapter)

        assert launch.command == DEFAULT_CURSOR_ACP_COMMAND
        assert launch.auth_method is None

    @pytest.mark.parametrize(
        ("config", "env"),
        [
            pytest.param(
                CursorACPAdapterConfig(api_key="key"),
                {"CURSOR_API_KEY": "key"},
                id="api-key",
            ),
            pytest.param(
                CursorACPAdapterConfig(auth_token="token", env={"OTHER": "x"}),
                {"OTHER": "x", "CURSOR_AUTH_TOKEN": "token"},
                id="auth-token-merged-into-env",
            ),
            pytest.param(
                CursorACPAdapterConfig(
                    api_key="shortcut", env={"CURSOR_API_KEY": "environment"}
                ),
                {"CURSOR_API_KEY": "environment"},
                id="env-wins-over-api-key",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_credentials_reach_the_agent_environment(
        self, config: CursorACPAdapterConfig, env: dict[str, str], tmp_path: Path
    ) -> None:
        launch = await launch_for(cursor_in(tmp_path, config))

        assert launch.env == env

    @pytest.mark.asyncio
    async def test_cwd_becomes_a_room_workspace_root(self, tmp_path: Path) -> None:
        adapter = CursorACPAdapter(CursorACPAdapterConfig(cwd=str(tmp_path)))

        launch = await launch_for(adapter, "room-a")

        assert launch.cwd == str(tmp_path / "room-a")
