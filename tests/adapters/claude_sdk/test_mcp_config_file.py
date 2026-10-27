"""The Claude CLI gets its Band MCP server, and that server's bearer token,
through a private config file, never through argv or env."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from band.integrations.claude_sdk.mcpconfig import MCP_CONFIG_DIR_PREFIX
from band.integrations.mcp import BandMCPTransport
from band.runtime.tools import BAND_MCP_SERVER_NAME
from tests.adapters.claude_sdk.fakecli import cli_mcp_config_file
from tests.adapters.claude_sdk.helpers import OpenRoom, sdk_cli_argv
from tests.adapters.roompeer import MCP_CONFIG_ARG
from tests.mcpclient import bearer_token


def posix_modes(config: Path) -> tuple[int, int]:
    """The file's and its directory's permission bits."""
    return (
        stat.S_IMODE(config.stat().st_mode),
        stat.S_IMODE(config.parent.stat().st_mode),
    )


def windows_location(config: Path) -> tuple[Path, bool]:
    """The directory holding the file's directory, and whether that one is
    this server's own."""
    return config.parent.parent, config.parent.name.startswith(MCP_CONFIG_DIR_PREFIX)


# Windows file modes are not access control: there the file is private by
# living in the user's own temp directory.
PRIVACY = [
    pytest.param(
        posix_modes,
        (0o600, 0o700),
        marks=pytest.mark.skipif(os.name == "nt", reason="POSIX modes"),
        id="posix-modes",
    ),
    pytest.param(
        windows_location,
        (Path(tempfile.gettempdir()), True),
        marks=pytest.mark.skipif(os.name != "nt", reason="Windows temp"),
        id="nt-user-temp",
    ),
]


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

    @pytest.mark.parametrize(("privacy", "private"), PRIVACY)
    async def test_the_file_is_private_to_this_user(
        self,
        claude_room: OpenRoom,
        privacy: Callable[[Path], object],
        private: object,
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("hi")])
        await room.send("hello")

        assert privacy(cli_mcp_config_file(room.claude.sessions[0].options)) == private

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

        crashed_url, replacement_url = room.session_band_urls
        assert room.chat == ["before", "after"]
        assert replacement_url == room.mcp_backend.endpoint(
            BandMCPTransport.HTTP, room.room_id
        )
        assert crashed_url != replacement_url

    async def test_leaving_a_room_removes_only_its_config_file(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        other_room = room.beside("room-2")
        room.claude.script(
            [room.model_reply("hi")], [other_room.model_reply("hi there")]
        )
        await room.send("hello")
        await other_room.send("hello")
        left_file, kept_file = (
            cli_mcp_config_file(cli.options) for cli in room.claude.sessions
        )

        await room.leave()

        assert (left_file.exists(), kept_file.exists()) == (False, True)

    async def test_cleanup_all_removes_the_config_files(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room()
        room.claude.script([room.model_reply("hi")])
        await room.send("hello")
        config = cli_mcp_config_file(room.claude.sessions[0].options)

        await room.adapter.cleanup_all()

        assert not config.parent.exists()
