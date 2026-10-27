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

    async def test_turns_on_an_unchanged_server_reuse_one_cli(
        self, claude_room: OpenRoom
    ) -> None:
        """The token reference is the same for every start, so an unchanged
        server never restarts the CLI."""
        room = await claude_room()
        room.claude.script([room.model_reply("one")], [room.model_reply("two")])

        await room.send("first")
        await room.send("second")

        assert room.chat == ["one", "two"]
        assert len(room.claude.sessions) == 1

    async def test_a_recovery_start_dials_the_replacement_server(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("before")])
        await room.send("first")
        await room.crash_band_server()
        room.claude.script([room.model_reply("after")])

        await room.send("second")

        crashed_url, replacement_url = room.session_band_urls
        assert room.chat == ["before", "after"]
        assert replacement_url == room.mcp_backend.endpoint(
            BandMCPTransport.HTTP, room.room_id
        )
        assert crashed_url != replacement_url
