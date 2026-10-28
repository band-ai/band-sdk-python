"""Extract complete native bundles while rejecting unsafe archive entries."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import ModuleType

import pytest

from tests.framework_conformance.antigravityinstaller.kit import write_bundle


class TestBundleExtraction:
    def test_preserves_complete_bundle_and_executable_attributes(
        self, installer: ModuleType, tmp_path: Path
    ) -> None:
        archive = write_bundle(
            tmp_path / "release.zip",
            {
                "server": (b"native server", stat.S_IFREG | 0o775),
                "companion": (b"native companion", stat.S_IFREG | 0o575),
                "resources/config": (b"configuration", stat.S_IFREG | 0o644),
            },
        )
        destination = tmp_path / "bundle"
        destination.mkdir()

        installer.extract_bundle(archive, destination)

        assert {
            str(path.relative_to(destination)).replace("\\", "/"): path.read_bytes()
            for path in destination.rglob("*")
            if path.is_file()
        } == {
            "server": b"native server",
            "companion": b"native companion",
            "resources/config": b"configuration",
        }

    @pytest.mark.skipif(
        os.name == "nt", reason="POSIX executable modes require a POSIX kernel"
    )
    def test_preserves_posix_native_executable_modes(
        self, installer: ModuleType, tmp_path: Path
    ) -> None:
        archive = write_bundle(
            tmp_path / "release.zip",
            {
                "server": (b"server", stat.S_IFREG | 0o775),
                "companion": (b"companion", stat.S_IFREG | 0o575),
            },
        )
        destination = tmp_path / "bundle"
        destination.mkdir()

        installer.extract_bundle(archive, destination)

        assert (
            stat.S_IMODE((destination / "server").stat().st_mode),
            stat.S_IMODE((destination / "companion").stat().st_mode),
        ) == (0o775, 0o575)

    @pytest.mark.parametrize(
        "name, mode",
        [
            ("../escaped", stat.S_IFREG | 0o644),
            ("/absolute", stat.S_IFREG | 0o644),
            ("C:/drive", stat.S_IFREG | 0o644),
            ("nested\\escaped", stat.S_IFREG | 0o644),
            ("link", stat.S_IFLNK | 0o777),
        ],
        ids=["traversal", "absolute", "drive", "backslash", "symlink"],
    )
    def test_rejects_unsafe_members(
        self, installer: ModuleType, tmp_path: Path, name: str, mode: int
    ) -> None:
        archive = write_bundle(tmp_path / "release.zip", {name: (b"payload", mode)})
        destination = tmp_path / "bundle"
        destination.mkdir()

        with pytest.raises(ValueError, match="unsafe Antigravity archive member"):
            installer.extract_bundle(archive, destination)

        assert list(destination.iterdir()) == []
