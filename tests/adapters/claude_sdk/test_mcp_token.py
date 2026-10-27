"""The Claude CLI gets the Band MCP server's bearer token through its
environment, never through its argv."""

from __future__ import annotations

import json

from band.adapters.claude_sdk import BAND_MCP_TOKEN_ENV, BAND_MCP_TOKEN_REF
from band.integrations.mcp import BandMCPTransport
from band.integrations.mcp.local_server import local_mcp_auth_headers
from band.runtime.tools import BAND_MCP_SERVER_NAME
from tests.adapters.claude_sdk.helpers import OpenRoom, sdk_cli_argv
from tests.adapters.roompeer import MCP_CONFIG_ARG, MCP_SERVERS_KEY


class TestMcpToken:
    async def test_cli_argv_references_the_token_the_cli_env_carries(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("hi")])
        await room.send("hello")

        [cli] = room.claude.sessions
        argv = sdk_cli_argv(cli.options)
        token = room.mcp_backend.auth_token
        assert json.loads(argv[argv.index(MCP_CONFIG_ARG) + 1]) == {
            MCP_SERVERS_KEY: {
                BAND_MCP_SERVER_NAME: {
                    "type": "http",
                    "url": room.mcp_backend.endpoint(
                        BandMCPTransport.HTTP, room.room_id
                    ),
                    "headers": local_mcp_auth_headers(BAND_MCP_TOKEN_REF),
                }
            }
        }
        assert not any(token in arg for arg in argv)
        assert cli.options.env[BAND_MCP_TOKEN_ENV] == token
