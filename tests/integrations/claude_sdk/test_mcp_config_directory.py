"""The private MCP config directory outlives a temp reaper, is replaced only
when it is no longer private, and a failed removal is retried."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import pytest

from band.integrations.claude_sdk import mcpconfig
from band.integrations.claude_sdk.mcpconfig import (
    REMOVAL_FAILED_LOG,
    REPLACED_DIRECTORY_LOG,
    McpConfigFiles,
)
from tests.adapters.claude_sdk.fakecli import read_mcp_config

SERVERS = {"band": {"type": "http", "url": "http://127.0.0.1:1/mcp"}}
CONFIG_LOGGER = mcpconfig.logger.name


class TestMcpConfigDirectory:
    def test_a_write_after_a_reaper_removed_the_directory_lands_in_a_new_one(
        self, mcp_config_files: McpConfigFiles, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A reaped directory is routine, so the switch is not logged."""
        caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
        reaped = mcp_config_files.write("room-a", SERVERS).parent
        shutil.rmtree(reaped)

        written = mcp_config_files.write("room-b", SERVERS)

        assert written.parent != reaped
        assert read_mcp_config(written) == SERVERS
        assert caplog.records == []

    def test_a_file_at_the_reaped_name_is_not_written_into_and_is_logged(
        self, mcp_config_files: McpConfigFiles, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The file is this user's own and private, so only its not being a
        directory rules it out."""
        caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
        reaped = mcp_config_files.write("room-a", SERVERS).parent
        shutil.rmtree(reaped)
        reaped.write_text("not a directory", encoding="utf-8")
        reaped.chmod(0o600)

        written = mcp_config_files.write("room-b", SERVERS)

        assert written.parent != reaped
        assert read_mcp_config(written) == SERVERS
        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.WARNING, REPLACED_DIRECTORY_LOG % reaped)
        ]

    def test_a_setgid_directory_stays_in_use_and_is_removed_whole(
        self, mcp_config_files: McpConfigFiles, tmp_path: Path
    ) -> None:
        """Under a setgid parent, Linux gives ``mkdtemp``'s directory the
        setgid bit; that bit grants no one access, so the directory is kept."""
        first = mcp_config_files.write("room-a", SERVERS)
        first.parent.chmod(0o2700)

        second = mcp_config_files.write("room-b", SERVERS)
        mcp_config_files.discard("room-a")
        mcp_config_files.remove()

        assert second.parent == first.parent
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize(
        "replaced",
        [
            pytest.param(
                True,
                marks=pytest.mark.skipif(os.name == "nt", reason="POSIX modes"),
                id="posix-not-trusted",
            ),
            pytest.param(
                False,
                marks=pytest.mark.skipif(os.name != "nt", reason="Windows temp"),
                id="nt-own-temp-trusted",
            ),
        ],
    )
    def test_a_directory_others_can_open_at_the_old_name_is_not_written_to(
        self, mcp_config_files: McpConfigFiles, replaced: bool
    ) -> None:
        """On POSIX another user may have taken the reaped directory's name in
        a shared ``/tmp``; on Windows the user's own temp directory is trusted."""
        reaped = mcp_config_files.write("room-a", SERVERS).parent
        shutil.rmtree(reaped)
        reaped.mkdir()
        reaped.chmod(0o755)

        written = mcp_config_files.write("room-b", SERVERS)

        assert (written.parent != reaped) is replaced

    def test_removing_a_reaped_directory_succeeds_quietly(
        self, mcp_config_files: McpConfigFiles, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
        shutil.rmtree(mcp_config_files.write("room-a", SERVERS).parent)

        mcp_config_files.remove()

        assert caplog.records == []

    def test_a_failed_removal_is_logged_and_retried_by_the_next_remove(
        self, mcp_config_files: McpConfigFiles, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A directory that cannot be removed now must not be forgotten, or
        ``stop()`` would leave it behind for good."""
        caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
        directory = mcp_config_files.write("room-a", SERVERS).parent
        shutil.rmtree(directory)
        directory.write_text("not a directory", encoding="utf-8")

        mcp_config_files.remove()
        refused = (
            directory.exists(),
            [(r.levelno, r.getMessage()) for r in caplog.records],
        )
        directory.unlink()
        directory.mkdir()
        mcp_config_files.remove()

        assert (refused, directory.exists()) == (
            (True, [(logging.WARNING, REMOVAL_FAILED_LOG % directory)]),
            False,
        )
