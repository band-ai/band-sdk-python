"""The Claude CLI gets its Band MCP server, and that server's bearer token,
through a private config file, never through argv or env."""

from __future__ import annotations

import os
import stat

import pytest

from band.integrations.mcp import BandMCPTransport
from band.runtime.tools import BAND_MCP_SERVER_NAME
from tests.adapters.claude_sdk.fakecli import cli_mcp_config_file
from tests.adapters.claude_sdk.helpers import OpenRoom, sdk_cli_argv
from tests.adapters.roompeer import MCP_CONFIG_ARG
from tests.mcpclient import bearer_token

POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt", reason="Windows file modes are not access control"
)


class TestMcpConfigFile:
    async def test_cli_argv_and_env_name_a_file_holding_the_band_server(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("hi")])
        await room.send("hello")

        [cli] = room.claude.sessions
        argv = sdk_cli_argv(cli.options)
        token = bearer_token(room.mcp_backend.headers)
        assert argv[argv.index(MCP_CONFIG_ARG) + 1] == str(
            cli_mcp_config_file(cli.options)
        )
        assert cli.mcp_servers == {
            BAND_MCP_SERVER_NAME: {
                "type": "http",
                "url": room.mcp_backend.endpoint(BandMCPTransport.HTTP, room.room_id),
                "headers": room.mcp_backend.headers,
            }
        }
        assert not any(token in arg for arg in argv)
        assert not any(token in value for value in cli.options.env.values())

    @POSIX_ONLY
    async def test_the_file_is_private_to_this_user(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("hi")])
        await room.send("hello")

        config = cli_mcp_config_file(room.claude.sessions[0].options)
        assert (
            stat.S_IMODE(config.stat().st_mode),
            stat.S_IMODE(config.parent.stat().st_mode),
        ) == (0o600, 0o700)

    async def test_turns_on_an_unchanged_server_reuse_one_cli(
        self, claude_room: OpenRoom
    ) -> None:
        """The session compares the servers it started with, not the path the
        CLI got, so an unchanged server never restarts the CLI."""
        room = await claude_room()
        room.claude.script([room.model_reply("one")], [room.model_reply("two")])

        await room.send("first")
        await room.send("second")

        assert room.chat == ["one", "two"]
        assert len(room.claude.sessions) == 1

    async def test_a_recovery_start_reads_the_replacement_server(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("before")])
        await room.send("first")
        await room.crash_band_server()
        room.claude.script([room.model_reply("after")])

        await room.send("second")

        first, second = room.claude.sessions
        assert room.chat == ["before", "after"]
        assert second.mcp_servers[BAND_MCP_SERVER_NAME]["url"] == (
            room.mcp_backend.endpoint(BandMCPTransport.HTTP, room.room_id)
        )
        assert (
            first.mcp_servers[BAND_MCP_SERVER_NAME]["url"]
            != (second.mcp_servers[BAND_MCP_SERVER_NAME]["url"])
        )

    async def test_cleanup_all_removes_the_config_files(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("hi")])
        await room.send("hello")
        config = cli_mcp_config_file(room.claude.sessions[0].options)

        await room.adapter.cleanup_all()

        assert not config.parent.exists()
