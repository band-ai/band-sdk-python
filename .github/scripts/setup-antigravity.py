#!/usr/bin/env python
"""Install a checksum-pinned native Antigravity bundle for the Google CI lane."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import platform
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import NamedTuple

logger = logging.getLogger(__name__)

VERSION = "1.4.3"
RELEASE_ROOT = "https://dl.google.com/agy-extensions/releases"


class Release(NamedTuple):
    """Identify a released bundle and its required companion."""

    url: str
    sha256: str
    companion: str


RELEASES = {
    "linux": Release(
        f"{RELEASE_ROOT}/linux/agy-acp-server-{VERSION}-linux-x86_64.zip",
        "0634ce5306ba92c932945455e7a83fda78e2d99c33fa8aa6ee0566206a382714",
        "localharness_external",
    ),
    "win32": Release(
        f"{RELEASE_ROOT}/windows/agy-acp-server-{VERSION}-windows-x86_64.zip",
        "54c2e16378d9a4952e1da70e355f3c33c4f284308d6de48ab3e6e34a127bd325",
        "localharness_external.exe",
    ),
}


def select_release(platform_id: str, machine: str) -> Release:
    """Reject runner platforms without a pinned release."""
    if machine.casefold() not in {"x86_64", "amd64"}:
        raise ValueError(f"unsupported Antigravity architecture: {machine}")
    try:
        return RELEASES[platform_id]
    except KeyError:
        raise ValueError(f"unsupported Antigravity platform: {platform_id}") from None


def native_command(platform_id: str, install_dir: Path) -> tuple[str, ...]:
    """Resolve the SDK launch policy to the installed native executable."""
    # The ACP extra is available in the Google lane, but not every unit-test lane.
    from band.adapters.antigravity_acp import default_antigravity_command  # noqa: PLC0415

    executable, *arguments = default_antigravity_command(platform_id)
    return str(install_dir / executable), *arguments


def download_archive(url: str, destination: Path) -> None:
    """Stream a release archive without buffering the large bundle in memory."""
    with (
        urllib.request.urlopen(url, timeout=120) as response,
        destination.open("wb") as output,
    ):
        shutil.copyfileobj(response, output)


def verify_archive(archive: Path, expected_sha256: str) -> None:
    """Verify cached and newly downloaded bytes before extraction."""
    with archive.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"Antigravity archive checksum mismatch: {archive}")


def cached_archive(
    release: Release,
    cache_dir: Path,
    download: Callable[[str, Path], None],
) -> Path:
    """Cache only verified archives; leave failed downloads outside the cache."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    archive = cache_dir / release.url.rsplit("/", 1)[-1]
    if archive.is_symlink():
        raise ValueError(f"archive cache must not be a symlink: {archive}")
    if archive.exists():
        verify_archive(archive, release.sha256)
        return archive
    with tempfile.TemporaryDirectory(prefix="download-", dir=cache_dir) as temporary:
        pending = Path(temporary) / archive.name
        download(release.url, pending)
        verify_archive(pending, release.sha256)
        pending.replace(archive)
    return archive


def extract_bundle(archive: Path, staging: Path) -> None:
    """Extract regular bundle files without traversal, links or duplicate paths."""
    seen: set[str] = set()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            name = member.orig_filename
            path = PurePosixPath(name)
            mode = member.external_attr >> 16
            key = name.rstrip("/").casefold()
            if (
                name != member.filename
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in name
                or PureWindowsPath(name).drive
                or key in seen
                or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}
            ):
                raise ValueError(f"unsafe Antigravity archive member: {name}")
            seen.add(key)
            destination = staging.joinpath(*path.parts)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, destination.open("xb") as output:
                shutil.copyfileobj(source, output)
            if member.create_system == 3 and mode:
                destination.chmod(stat.S_IMODE(mode))


def install(
    *,
    release: Release,
    platform_id: str,
    cache_dir: Path,
    install_dir: Path,
    github_env: Path,
    github_path: Path,
    download: Callable[[str, Path], None] = download_archive,
) -> tuple[str, ...]:
    """Install a fresh complete bundle and export its exact native argv."""
    if install_dir.is_symlink() or (
        install_dir.exists()
        and (not install_dir.is_dir() or any(install_dir.iterdir()))
    ):
        raise ValueError(
            f"refusing to overwrite Antigravity installation: {install_dir}"
        )
    install_dir = install_dir.resolve()
    if any(character in str(install_dir) for character in "\r\n"):
        raise ValueError("Antigravity install path must not contain line breaks")
    command = native_command(platform_id, install_dir)
    archive = cached_archive(release, cache_dir, download)
    install_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="antigravity-", dir=install_dir.parent
    ) as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir()
        extract_bundle(archive, staging)
        for required in (Path(command[0]).name, release.companion):
            if not (staging / required).is_file():
                raise ValueError(
                    f"Antigravity bundle is missing required file: {required}"
                )
        if install_dir.exists():
            install_dir.rmdir()
        staging.rename(install_dir)
    with github_env.open("a", encoding="utf-8") as output:
        output.write(f"ANTIGRAVITY_COMMAND={json.dumps(command)}\n")
    with github_path.open("a", encoding="utf-8") as output:
        output.write(f"{install_dir}\n")
    logger.info("Installed Antigravity %s in %s", VERSION, install_dir)
    return command


def main(argv: Sequence[str] | None = None) -> None:
    """Install for the current runner using explicit GitHub output paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cache-dir", "install-dir", "github-env", "github-path"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args(argv)
    install(
        release=select_release(sys.platform, platform.machine()),
        platform_id=sys.platform,
        cache_dir=args.cache_dir,
        install_dir=args.install_dir,
        github_env=args.github_env,
        github_path=args.github_path,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
