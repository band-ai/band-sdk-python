"""The settings ``ACPClientAdapterConfig`` validates and the launch they produce."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from acp.schema import (
    McpServerStdio,
    SseMcpServer,
)
from pydantic import ValidationError

from band.integrations.acp.client_adapter import (
    ACPClientAdapter,
    ACPClientAdapterConfig,
)
from tests.integrations.acp.acp_toolkit.harness import (
    Launch,
    launch_for,
)
from tests.integrations.acp.clientadapter.kit import CODEX


class TestACPClientAdapterConfig:
    """The settings ``ACPClientAdapterConfig`` validates and the launch they produce."""

    @pytest.mark.asyncio
    async def test_a_string_command_launches_as_one_argument(
        self, tmp_path: Path
    ) -> None:
        adapter = ACPClientAdapter(CODEX, workspace_for_room=lambda _: str(tmp_path))

        launch = await launch_for(adapter)

        assert launch.command == ("codex",)

    @pytest.mark.asyncio
    async def test_settings_reach_the_launched_agent(self, tmp_path: Path) -> None:
        config = ACPClientAdapterConfig(
            command=["gemini", "cli"],
            env={"API_KEY": "test"},
            auth_method="api_key",
            use_unstable_protocol=True,
        )
        adapter = ACPClientAdapter(config, workspace_for_room=lambda _: str(tmp_path))

        launch = await launch_for(adapter)

        assert launch == Launch(
            command=("gemini", "cli"),
            env={"API_KEY": "test"},
            cwd=str(tmp_path),
            use_unstable_protocol=True,
            auth_method="api_key",
            client_capabilities=None,
        )

    def test_command_is_required(self) -> None:
        with pytest.raises(ValidationError, match="command\n  Field required"):
            ACPClientAdapterConfig.model_validate({})

    @pytest.mark.parametrize("command", [[], ""])
    def test_an_empty_command_is_rejected(self, command: list[str] | str) -> None:
        with pytest.raises(ValueError, match="ACP stdio transport requires a command"):
            ACPClientAdapterConfig(command=command)

    @pytest.mark.parametrize(
        "setting",
        [{"host": "10.0.0.5", "port": 8080}, {"host": "10.0.0.5"}, {"port": 8080}],
        ids=["host-and-port", "host", "port"],
    )
    def test_a_shared_tcp_process_is_rejected(self, setting: dict[str, object]) -> None:
        with pytest.raises(ValueError, match="TCP ACP transport cannot guarantee"):
            ACPClientAdapterConfig.model_validate({"command": "codex", **setting})

    def test_host_mcp_servers_load_as_acp_servers(self) -> None:
        config = ACPClientAdapterConfig.model_validate(
            {
                "command": "codex",
                "mcp_servers": [
                    {"name": "fs", "command": "npx", "args": ["fs"], "env": []},
                    {
                        "type": "sse",
                        "name": "band",
                        "url": "http://h/sse",
                        "headers": [],
                    },
                ],
            }
        )

        assert config.mcp_servers == (
            McpServerStdio(name="fs", command="npx", args=["fs"], env=[]),
            SseMcpServer(type="sse", name="band", url="http://h/sse", headers=[]),
        )

    def test_a_malformed_mcp_server_fails_at_load_not_at_session_start(self) -> None:
        with pytest.raises(ValidationError, match="mcp_servers.0"):
            ACPClientAdapterConfig.model_validate(
                {"command": "codex", "mcp_servers": [{"name": "fs"}]}
            )

    @pytest.mark.parametrize("turn_timeout_s", [0, -1.0])
    def test_turn_timeout_must_be_positive(self, turn_timeout_s: float) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            ACPClientAdapterConfig(command="codex", turn_timeout_s=turn_timeout_s)

    def test_a_typed_selection_and_a_resolver_are_exclusive(self) -> None:
        config = ACPClientAdapterConfig(command="codex", reasoning_effort="high")

        with pytest.raises(ValueError, match="not both"):
            ACPClientAdapter(config, resolve_session_config=AsyncMock())

    def test_a_custom_transport_is_rejected(self) -> None:
        with pytest.raises(
            ValueError,
            match="custom ACP transports cannot guarantee room process isolation",
        ):
            ACPClientAdapter(CODEX, spawn_process=object())

    def test_starts_with_no_room_state(self) -> None:
        adapter = ACPClientAdapter(CODEX)

        assert adapter._rooms == {}
        assert adapter._workspaces.rooms == ()

    def test_retired_acp_delivery_mode_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            ACPClientAdapterConfig.model_validate(
                {"command": "codex", "assistant_text_mode": "thought"}
            )
