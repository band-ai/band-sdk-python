"""The SDK-owned plugin projects MCP servers and connection rules for one process."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from band.integrations.ompprocess import (
    OmpMcpServer,
    omp_mcp_arguments,
    omp_mcp_plugin,
    omp_mcp_ready_command,
)


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
            config = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
            assert config["mcpServers"]["remote"] == {
                "type": "sse",
                "url": "https://mcp.example/sse",
            }
            local = config["mcpServers"]["local"]
            assert local["args"] == ["--port", "1"]
            assert Path(local["cwd"]) == tmp_path.resolve()
            assert Path(local["command"]) == (tmp_path / "server").resolve()
            startup = (root / "startup.js").read_text(encoding="utf-8")
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

    def test_arguments_load_the_plugin_and_its_startup_extension(self) -> None:
        plugin = "/tmp/overlay/band-sdk"
        arguments = omp_mcp_arguments(plugin)
        assert arguments[:2] == ("--plugin-dir", plugin)
        assert "--mode=rpc-ui" in arguments
        assert str(Path(plugin) / "startup.js") in arguments
        assert str(Path(plugin) / "startup.json") in arguments

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
