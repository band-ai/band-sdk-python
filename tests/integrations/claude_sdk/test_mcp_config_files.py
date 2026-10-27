"""A room's MCP config file is replaced whole or not at all."""

from __future__ import annotations

import pytest

from band.integrations.claude_sdk.mcpconfig import McpConfigFiles
from tests.adapters.claude_sdk.fakecli import read_mcp_config

SERVER = {"type": "http", "url": "http://127.0.0.1:1/mcp"}


class TestMcpConfigFiles:
    def test_a_failed_write_keeps_the_previous_file_and_leaves_no_partial(
        self, mcp_config_files: McpConfigFiles
    ) -> None:
        path = mcp_config_files.write("room-a", {"band": SERVER})

        with pytest.raises(TypeError):
            mcp_config_files.write("room-a", {"band": SERVER, "broken": object()})

        assert (list(path.parent.iterdir()), read_mcp_config(path)) == (
            [path],
            {"band": SERVER},
        )
