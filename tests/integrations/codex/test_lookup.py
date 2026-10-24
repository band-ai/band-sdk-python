"""Parent launch context determines Windows executable selection."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from band.integrations.codex.winspawn import command_in_job
from tests.integrations.codex.lookup import WindowsLookup, selected_executable


class TestWindowsExecutableLookup:
    def test_helper_uses_the_running_image_instead_of_a_venv_launcher(
        self, windows_lookup: WindowsLookup
    ) -> None:
        peer = windows_lookup
        command = command_in_job([str(peer.image)], "test-job", 1)
        assert Path(command[0]).samefile(peer.image)

    @pytest.mark.parametrize(
        "first",
        range(6),
        ids=["application", "parent-cwd", "system", "legacy-system", "windows", "path"],
    )
    def test_earliest_native_search_directory_wins(
        self, windows_lookup: WindowsLookup, first: int
    ) -> None:
        peer = windows_lookup
        for directory in peer.directories[first:]:
            (directory / "probe.exe").touch()
        assert selected_executable("probe").samefile(
            peer.directories[first] / "probe.exe"
        )

    @pytest.mark.parametrize("parent_path", [None, ""])
    def test_system_executable_does_not_require_path(
        self, windows_lookup: WindowsLookup, parent_path: str | None
    ) -> None:
        peer = windows_lookup
        peer.parent_path = parent_path
        expected = peer.system / "probe.exe"
        expected.touch()
        assert selected_executable("probe.exe").samefile(expected)

    def test_current_directory_can_be_excluded(
        self, windows_lookup: WindowsLookup
    ) -> None:
        peer = windows_lookup
        peer.search_current = False
        (peer.current / "probe.exe").touch()
        expected = peer.system / "probe.exe"
        expected.touch()
        assert selected_executable("probe.exe").samefile(expected)

    def test_long_parent_path_is_not_truncated(
        self, windows_lookup: WindowsLookup
    ) -> None:
        peer = windows_lookup
        peer.parent_path = os.pathsep.join(
            [str(peer.path / "missing")] * 20 + [str(peer.path)]
        )
        expected = peer.path / "probe.exe"
        expected.touch()
        assert selected_executable("probe.exe").samefile(expected)
