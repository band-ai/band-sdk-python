"""In-process ``sdk`` servers stay in the CLI options' dict; servers the CLI
dials, and the headers they carry, reach it only through a private file."""

from __future__ import annotations

from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server

from band.integrations.claude_sdk.mcpconfig import MCP_CONFIG_FLAG
from band.integrations.claude_sdk.session_manager import ClaudeSessionManager
from band.integrations.mcp.local_server import local_mcp_auth_headers
from tests.adapters.claude_sdk.fakecli import (
    MODEL,
    FakeClaude,
    cli_mcp_config_file,
    read_mcp_config,
)
from tests.adapters.claude_sdk.helpers import exchange, sdk_cli_argv
from tests.adapters.roompeer import MCP_CONFIG_ARG
from tests.integrations.claude_sdk.sessionmanager.kit import thinking

TOKEN = "split-test-token"
BAND_SERVER: dict[str, Any] = {
    "type": "http",
    "url": "http://127.0.0.1:1/rooms/room-a/mcp",
    "headers": local_mcp_auth_headers(TOKEN),
}


class TestMcpServerSplit:
    def test_a_caller_mcp_config_file_is_refused_up_front(self) -> None:
        """It would replace the private file the dialed servers go to."""
        with pytest.raises(ValueError, match=MCP_CONFIG_FLAG):
            ClaudeSessionManager(
                ClaudeAgentOptions(
                    model=MODEL, extra_args={MCP_CONFIG_FLAG: "servers.json"}
                )
            )

    async def test_a_factoryless_sdk_server_stays_inline(
        self, claude: FakeClaude
    ) -> None:
        in_process = create_sdk_mcp_server("in-process", tools=[])
        manager = ClaudeSessionManager(
            ClaudeAgentOptions(model=MODEL, mcp_servers={"in-process": in_process})
        )
        claude.script(thinking("hi"))
        try:
            await exchange(manager.room("room-a"), "hi")
        finally:
            await manager.stop()

        [cli] = claude.sessions
        assert cli.options.mcp_servers == {"in-process": in_process}
        assert MCP_CONFIG_FLAG not in cli.options.extra_args

    async def test_a_mixed_set_keeps_sdk_inline_and_dialed_servers_in_the_file(
        self, claude: FakeClaude
    ) -> None:
        in_process = create_sdk_mcp_server("in-process", tools=[])
        manager = ClaudeSessionManager(
            ClaudeAgentOptions(model=MODEL),
            mcp_servers_factory=lambda _room: {
                "band": BAND_SERVER,
                "in-process": in_process,
            },
        )
        claude.script(thinking("hi"))
        try:
            await exchange(manager.room("room-a"), "hi")
            [cli] = claude.sessions
            in_file = read_mcp_config(cli_mcp_config_file(cli.options))
        finally:
            await manager.stop()

        argv = sdk_cli_argv(cli.options)
        assert cli.options.mcp_servers == {"in-process": in_process}
        assert in_file == {"band": BAND_SERVER}
        assert argv.count(MCP_CONFIG_ARG) == 2
        assert not any(TOKEN in arg for arg in argv)
