"""The SDK-owned plugin projects MCP servers and connection rules for one process."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from band.integrations.ompprocess import (
    OMP_MCP_SERVERS_CONFIG,
    OMP_MCP_STARTUP_CONFIG,
    OMP_MCP_STARTUP_JS,
    RELATIVE_MCP_COMMAND_PREFIXES,
    OmpMcpServer,
    OmpMcpTransport,
    omp_mcp_plugin,
)
from tests.ostable import skip_unless_os


def plugin_privacy(root: Path, files: tuple[Path, ...]) -> tuple[int, int | None]:
    file_modes = {stat.S_IMODE(path.stat().st_mode) for path in files}
    file_mode = next(iter(file_modes)) if len(file_modes) == 1 else None
    return (stat.S_IMODE(root.stat().st_mode), file_mode)


class TestMcpPluginProjection:
    @pytest.mark.parametrize("system_prompt", ["", "   "])
    def test_nothing_to_project_yields_no_plugin(
        self, tmp_path: Path, system_prompt: str
    ) -> None:
        with omp_mcp_plugin(
            {}, workspace=str(tmp_path), system_prompt=system_prompt
        ) as plugin:
            assert plugin is None

    def test_servers_land_in_the_plugin_mcp_config(self, tmp_path: Path) -> None:
        servers = {
            "local": OmpMcpServer(command="./server", args=("--port", "1")),
            "remote": OmpMcpServer(
                type=OmpMcpTransport.SSE, url="https://mcp.example/sse"
            ),
        }
        with omp_mcp_plugin(servers, workspace=str(tmp_path)) as plugin:
            assert plugin is not None
            root = Path(plugin)

            config = json.loads(
                (root / OMP_MCP_SERVERS_CONFIG).read_text(encoding="utf-8")
            )
            startup_config = json.loads(
                (root / OMP_MCP_STARTUP_CONFIG).read_text(encoding="utf-8")
            )
            assert startup_config == {"startup": {"quiet": False}}
            assert config["mcpServers"]["remote"] == {
                "type": OmpMcpTransport.SSE,
                "url": "https://mcp.example/sse",
            }
            local = config["mcpServers"]["local"]
            assert local["args"] == ["--port", "1"]
            assert Path(local["cwd"]) == tmp_path.resolve()
            assert Path(local["command"]) == (tmp_path / "server").resolve()

        assert not Path(plugin).exists()

    def test_connection_instructions_become_an_always_applied_rule(
        self, tmp_path: Path
    ) -> None:
        with omp_mcp_plugin(
            {}, workspace=str(tmp_path), system_prompt="Reply through Band."
        ) as plugin:
            assert plugin is not None
            rules = list((Path(plugin) / "rules").glob("band-connection-*.md"))
            assert len(rules) == 1
            assert rules[0].read_text(encoding="utf-8") == (
                "---\nalwaysApply: true\n---\nReply through Band."
            )

    @pytest.mark.parametrize("accepted_on", [("posix",)])
    def test_projected_plugin_files_are_owner_only(
        self, tmp_path: Path, accepted_on: tuple[str, ...]
    ) -> None:
        skip_unless_os(accepted_on)
        servers = {"local": OmpMcpServer(command="./server")}
        with omp_mcp_plugin(servers, workspace=str(tmp_path)) as plugin:
            assert plugin is not None
            root = Path(plugin)
            files = (
                root / OMP_MCP_SERVERS_CONFIG,
                root / OMP_MCP_STARTUP_CONFIG,
                root / OMP_MCP_STARTUP_JS,
            )
            assert root.is_dir()
            assert all(path.is_file() for path in files)
            assert plugin_privacy(root, files) == (0o700, 0o600)

    @pytest.mark.parametrize("accepted_on", [("nt",)])
    def test_projected_plugin_files_are_readable(
        self, tmp_path: Path, accepted_on: tuple[str, ...]
    ) -> None:
        skip_unless_os(accepted_on)
        servers = {"local": OmpMcpServer(command="./server")}
        with omp_mcp_plugin(servers, workspace=str(tmp_path)) as plugin:
            assert plugin is not None
            root = Path(plugin)
            files = (
                root / OMP_MCP_SERVERS_CONFIG,
                root / OMP_MCP_STARTUP_CONFIG,
                root / OMP_MCP_STARTUP_JS,
            )
            assert root.is_dir()
            assert all(path.is_file() for path in files)
            assert all(os.access(path, os.R_OK) for path in files)

    @pytest.mark.parametrize("prefix", RELATIVE_MCP_COMMAND_PREFIXES)
    def test_relative_command_is_absolutized(self, tmp_path: Path, prefix: str) -> None:
        servers = {"local": OmpMcpServer(command=f"{prefix}server")}
        with omp_mcp_plugin(servers, workspace=str(tmp_path)) as plugin:
            assert plugin is not None
            config = json.loads(
                (Path(plugin) / OMP_MCP_SERVERS_CONFIG).read_text(encoding="utf-8")
            )
            assert Path(config["mcpServers"]["local"]["command"]).is_absolute()

    @pytest.mark.parametrize("transport", [None, OmpMcpTransport.STDIO])
    def test_command_transport_projects_a_launchable_server(
        self, tmp_path: Path, transport: OmpMcpTransport | None
    ) -> None:
        servers = {
            "local": OmpMcpServer(
                type=transport, command="server", args=("--port", "1")
            )
        }
        with omp_mcp_plugin(servers, workspace=str(tmp_path)) as plugin:
            assert plugin is not None
            config = json.loads(
                (Path(plugin) / OMP_MCP_SERVERS_CONFIG).read_text(encoding="utf-8")
            )
            local = config["mcpServers"]["local"]
            assert local.get("type") == transport
            assert local["command"] == "server"
            assert local["args"] == ["--port", "1"]
            assert Path(local["cwd"]) == tmp_path.resolve()

    @pytest.mark.parametrize(
        "transport", [None, OmpMcpTransport.HTTP, OmpMcpTransport.SSE]
    )
    def test_url_transport_preserves_the_remote_endpoint(
        self, tmp_path: Path, transport: OmpMcpTransport | None
    ) -> None:
        servers = {
            "remote": OmpMcpServer(
                type=transport,
                url="https://mcp.example/tools",
                headers={"Authorization": "Bearer token"},
            )
        }
        with omp_mcp_plugin(servers, workspace=str(tmp_path)) as plugin:
            assert plugin is not None
            config = json.loads(
                (Path(plugin) / OMP_MCP_SERVERS_CONFIG).read_text(encoding="utf-8")
            )
            remote = config["mcpServers"]["remote"]
            assert remote.get("type") == transport
            assert remote["url"] == "https://mcp.example/tools"
            assert remote["headers"] == {"Authorization": "Bearer token"}
            assert "command" not in remote
            assert "cwd" not in remote

    @pytest.mark.parametrize(
        "fields",
        [
            pytest.param({}, id="no-endpoint"),
            pytest.param(
                {"command": "server", "url": "https://mcp.example"},
                id="two-endpoints",
            ),
            pytest.param(
                {"command": "server", "type": OmpMcpTransport.HTTP},
                id="command-http",
            ),
            pytest.param(
                {"command": "server", "type": OmpMcpTransport.SSE},
                id="command-sse",
            ),
            pytest.param(
                {"url": "https://mcp.example", "type": OmpMcpTransport.STDIO},
                id="url-stdio",
            ),
            pytest.param(
                {"command": "server", "type": "websocket"},
                id="unknown-transport",
            ),
        ],
    )
    def test_conflicting_transport_settings_are_rejected(
        self, fields: dict[str, str]
    ) -> None:
        with pytest.raises(ValidationError):
            OmpMcpServer.model_validate(fields)
