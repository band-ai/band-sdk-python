"""The private MCP config directory outlives a temp reaper, is replaced only
when it is no longer the directory this process created, and a failed
deletion is logged, not raised, and retried."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import pytest

from band.integrations.claude_sdk import mcpconfig
from band.integrations.claude_sdk.mcpconfig import (
    DISCARD_FAILED_LOG,
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
        """The file is this user's own and private; it is still not the
        directory this process created."""
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

    def test_another_directory_at_the_name_is_not_written_into_and_is_logged(
        self,
        mcp_config_files: McpConfigFiles,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """Moving ours away first keeps its inode taken, so the directory now
        at the name is certainly a different one."""
        caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
        original = mcp_config_files.write("room-a", SERVERS).parent
        original.rename(tmp_path / "moved")
        original.mkdir(mode=0o700)

        written = mcp_config_files.write("room-b", SERVERS)

        assert written.parent != original
        assert list(original.iterdir()) == []
        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.WARNING, REPLACED_DIRECTORY_LOG % original)
        ]

    @pytest.mark.skipif(os.name == "nt", reason="Windows symlinks need privilege")
    def test_a_symlink_at_the_reaped_name_is_not_followed_and_is_logged(
        self,
        mcp_config_files: McpConfigFiles,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
    ) -> None:
        """The link points at a private directory of this user, which is still
        not the directory this process created."""
        caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
        target = tmp_path / "elsewhere"
        target.mkdir(mode=0o700)
        reaped = mcp_config_files.write("room-a", SERVERS).parent
        shutil.rmtree(reaped)
        reaped.symlink_to(target, target_is_directory=True)

        written = mcp_config_files.write("room-b", SERVERS)

        assert written.parent not in (reaped, target)
        assert list(target.iterdir()) == []
        assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
            (logging.WARNING, REPLACED_DIRECTORY_LOG % reaped)
        ]

    @pytest.mark.parametrize(
        "mode",
        [
            # Linux gives mkdtemp's directory a setgid parent's setgid bit.
            pytest.param(0o2700, id="setgid"),
            # vfat, CIFS and WSL drvfs report fixed modes like this.
            pytest.param(0o755, id="fixed-mode-filesystem"),
        ],
    )
    def test_our_directory_stays_in_use_whatever_its_mode_bits(
        self, mcp_config_files: McpConfigFiles, tmp_path: Path, mode: int
    ) -> None:
        """Identity, not mode bits, decides reuse, so nothing is orphaned."""
        first = mcp_config_files.write("room-a", SERVERS)
        first.parent.chmod(mode)

        second = mcp_config_files.write("room-b", SERVERS)
        mcp_config_files.discard("room-a")
        mcp_config_files.remove()

        assert second.parent == first.parent
        assert list(tmp_path.iterdir()) == []

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

    def test_a_file_that_cannot_be_discarded_is_logged_and_removed_later(
        self, mcp_config_files: McpConfigFiles, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Disposal goes on past the failure; ``remove()`` takes the file."""
        caplog.set_level(logging.WARNING, logger=CONFIG_LOGGER)
        stuck = mcp_config_files.write("room-a", SERVERS)
        stuck.unlink()
        stuck.mkdir()

        mcp_config_files.discard("room-a")
        logged = [(r.levelno, r.getMessage()) for r in caplog.records]
        mcp_config_files.remove()

        assert logged == [(logging.WARNING, DISCARD_FAILED_LOG % stuck)]
        assert not stuck.parent.exists()
