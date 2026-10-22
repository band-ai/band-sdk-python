"""The SDK-owned plugin projects MCP servers and connection rules for one process."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from band.integrations.ompprocess import (
    OMP_MCP_SERVERS_CONFIG,
    OMP_MCP_STARTUP_CONFIG,
    OMP_MCP_STARTUP_JS,
    OMP_MODE_FLAG,
    OMP_RPC_UI_MODE,
    RELATIVE_MCP_COMMAND_PREFIXES,
    OmpMcpServer,
    omp_mcp_arguments,
    omp_mcp_plugin,
    omp_mcp_ready_command,
)


def plugin_privacy(root: Path, files: tuple[Path, ...]) -> tuple[int, int | None]:
    file_modes = {stat.S_IMODE(path.stat().st_mode) for path in files}
    file_mode = next(iter(file_modes)) if len(file_modes) == 1 else None
    return (stat.S_IMODE(root.stat().st_mode), file_mode)


class TestMcpPluginProjection:
    def test_nothing_to_project_yields_no_plugin(self, tmp_path: Path) -> None:
        with omp_mcp_plugin({}, workspace=str(tmp_path)) as plugin:
            assert plugin is None

    def test_servers_land_in_the_plugin_mcp_config(self, tmp_path: Path) -> None:
        servers = {
            "local": OmpMcpServer(command="./server", args=("--port", "1")),
            "remote": OmpMcpServer(type="sse", url="https://mcp.example/sse"),
        }
        with omp_mcp_plugin(servers, workspace=str(tmp_path)) as plugin:
            assert plugin is not None
            root = Path(plugin)
            assert root.name == "band-sdk"
            config = json.loads(
                (root / OMP_MCP_SERVERS_CONFIG).read_text(encoding="utf-8")
            )
            startup_config = json.loads(
                (root / OMP_MCP_STARTUP_CONFIG).read_text(encoding="utf-8")
            )
            assert startup_config == {"startup": {"quiet": False}}
            assert config["mcpServers"]["remote"] == {
                "type": "sse",
                "url": "https://mcp.example/sse",
            }
            local = config["mcpServers"]["local"]
            assert local["args"] == ["--port", "1"]
            assert Path(local["cwd"]) == tmp_path.resolve()
            assert Path(local["command"]) == (tmp_path / "server").resolve()
            startup = (root / OMP_MCP_STARTUP_JS).read_text(encoding="utf-8")
            assert omp_mcp_ready_command(plugin) in startup
            assert '"band-sdk:local"' in startup
            assert '"band-sdk:remote"' in startup
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

    def test_arguments_load_the_plugin_and_its_startup_extension(
        self, tmp_path: Path
    ) -> None:
        plugin = str(tmp_path / "band-sdk")
        arguments = omp_mcp_arguments(plugin)
        assert arguments[:2] == ("--plugin-dir", plugin)
        assert arguments[arguments.index(OMP_MODE_FLAG) + 1] == OMP_RPC_UI_MODE
        assert str(Path(plugin) / OMP_MCP_STARTUP_JS) in arguments
        assert str(Path(plugin) / OMP_MCP_STARTUP_CONFIG) in arguments

    @pytest.mark.parametrize("accepted_on", [("posix",)])
    def test_projected_plugin_files_are_owner_only(
        self, tmp_path: Path, accepted_on: tuple[str, ...]
    ) -> None:
        if os.name not in accepted_on:
            pytest.skip("Unix permission bits are not the Windows ACL boundary")
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

    @pytest.mark.parametrize("prefix", RELATIVE_MCP_COMMAND_PREFIXES)
    def test_relative_command_is_absolutized(self, tmp_path: Path, prefix: str) -> None:
        servers = {"local": OmpMcpServer(command=f"{prefix}server")}
        with omp_mcp_plugin(servers, workspace=str(tmp_path)) as plugin:
            assert plugin is not None
            config = json.loads(
                (Path(plugin) / OMP_MCP_SERVERS_CONFIG).read_text(encoding="utf-8")
            )
            assert Path(config["mcpServers"]["local"]["command"]).is_absolute()

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"command": "server", "url": "https://mcp.example"},
            {"command": "server", "type": "http"},
            {"url": "https://mcp.example", "type": "stdio"},
        ],
    )
    def test_a_server_needs_exactly_one_transport(self, fields: dict[str, str]) -> None:
        with pytest.raises(ValueError):
            OmpMcpServer(**fields)
