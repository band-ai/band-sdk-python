"""Install verified complete bundles and export direct native commands."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from types import ModuleType

import pytest

from tests.framework_conformance.antigravityinstaller.kit import (
    ArchiveDownload,
    write_bundle,
)

pytestmark = pytest.mark.usefixtures("acp_extra")


class TestNativeInstallation:
    @pytest.mark.parametrize("platform_id", ["linux", "win32"])
    def test_downloads_once_and_extracts_fresh_bundles_with_exact_exports(
        self, installer: ModuleType, tmp_path: Path, platform_id: str
    ) -> None:
        first = tmp_path / "first installation with spaces"
        command = installer.native_command(platform_id, first)
        release = installer.select_release(platform_id, "AMD64")
        members = {
            Path(command[0]).name: (b"server", stat.S_IFREG | 0o775),
            release.companion: (b"companion", stat.S_IFREG | 0o575),
            "resources/data": (b"resource", stat.S_IFREG | 0o644),
        }
        archive = write_bundle(tmp_path / "release.zip", members)
        release = release._replace(
            sha256=hashlib.sha256(archive.read_bytes()).hexdigest()
        )
        download = ArchiveDownload(release.url, archive)
        environment = tmp_path / "github-env"
        path = tmp_path / "github-path"
        environment.write_text("EXISTING=value\n", encoding="utf-8")
        path.write_text("existing-path\n", encoding="utf-8")

        installed = installer.install(
            release=release,
            platform_id=platform_id,
            cache_dir=tmp_path / "cache",
            install_dir=first,
            github_env=environment,
            github_path=path,
            download=download,
        )
        (first / "resources/data").write_bytes(b"locally modified")
        second = tmp_path / "second installation"
        second.mkdir()
        reused = installer.install(
            release=release,
            platform_id=platform_id,
            cache_dir=tmp_path / "cache",
            install_dir=second,
            github_env=environment,
            github_path=path,
            download=download,
        )

        assert installed == command
        assert reused == installer.native_command(platform_id, second)
        assert (second / release.companion).read_bytes() == b"companion"
        assert (second / "resources/data").read_bytes() == b"resource"
        assert environment.read_text(encoding="utf-8") == (
            f"EXISTING=value\nANTIGRAVITY_COMMAND={json.dumps(installed)}\n"
            f"ANTIGRAVITY_COMMAND={json.dumps(reused)}\n"
        )
        assert path.read_text(encoding="utf-8") == f"existing-path\n{first}\n{second}\n"
        assert [file.name for file in (tmp_path / "cache").iterdir()] == [
            release.url.rsplit("/", 1)[-1]
        ]

    @pytest.mark.parametrize(
        ("archive_parent", "downloaded"),
        [("", True), ("cache", False)],
        ids=["download", "cache-hit"],
    )
    def test_rejects_checksum_mismatch_without_installing_or_exporting(
        self,
        installer: ModuleType,
        tmp_path: Path,
        archive_parent: str,
        downloaded: bool,
    ) -> None:
        release = installer.select_release("linux", "x86_64")
        cache = tmp_path / "cache"
        cache.mkdir()
        archive = tmp_path / archive_parent / release.url.rsplit("/", 1)[-1]
        archive.write_bytes(b"corrupt archive")
        download = ArchiveDownload(release.url, archive)

        with pytest.raises(ValueError, match="checksum mismatch"):
            installer.install(
                release=release,
                platform_id="linux",
                cache_dir=cache,
                install_dir=tmp_path / "installed",
                github_env=tmp_path / "env",
                github_path=tmp_path / "path",
                download=download,
            )

        assert not (tmp_path / "installed").exists()
        assert not (tmp_path / "env").exists()
        assert not (tmp_path / "path").exists()
        assert download.requested == downloaded

    @pytest.mark.parametrize("missing", ["server", "companion"])
    def test_rejects_incomplete_bundle_without_installing(
        self, installer: ModuleType, tmp_path: Path, missing: str
    ) -> None:
        release = installer.select_release("linux", "x86_64")
        command = installer.native_command("linux", tmp_path / "installed")
        members = {"server": Path(command[0]).name, "companion": release.companion}
        del members[missing]
        archive = write_bundle(
            tmp_path / "release.zip",
            {name: (b"native", stat.S_IFREG | 0o755) for name in members.values()},
        )
        release = release._replace(
            sha256=hashlib.sha256(archive.read_bytes()).hexdigest()
        )

        with pytest.raises(ValueError, match="missing required file"):
            installer.install(
                release=release,
                platform_id="linux",
                cache_dir=tmp_path / "cache",
                install_dir=tmp_path / "installed",
                github_env=tmp_path / "env",
                github_path=tmp_path / "path",
                download=ArchiveDownload(release.url, archive),
            )

        assert not (tmp_path / "installed").exists()
        assert not (tmp_path / "env").exists()
        assert not (tmp_path / "path").exists()
        assert sorted(file.name for file in tmp_path.iterdir()) == [
            "cache",
            "release.zip",
        ]

    def test_refuses_to_clobber_an_existing_installation(
        self, installer: ModuleType, tmp_path: Path
    ) -> None:
        target = tmp_path / "installed"
        target.mkdir()
        (target / "owned-by-user").write_bytes(b"keep me")

        with pytest.raises(ValueError, match="refusing to overwrite"):
            installer.install(
                release=installer.select_release("linux", "x86_64"),
                platform_id="linux",
                cache_dir=tmp_path / "cache",
                install_dir=target,
                github_env=tmp_path / "env",
                github_path=tmp_path / "path",
            )

        assert (target / "owned-by-user").read_bytes() == b"keep me"
        assert sorted(file.name for file in tmp_path.iterdir()) == ["installed"]
