"""Build real release-shaped archives at the download boundary."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path


def write_bundle(path: Path, members: dict[str, tuple[bytes, int]]) -> Path:
    """Write a Unix-attributed ZIP without simulating installer behavior."""
    with zipfile.ZipFile(path, "w") as bundle:
        for name, (content, mode) in members.items():
            member = zipfile.ZipInfo(name)
            # ZipInfo normalizes separators on Windows; preserve the wire input.
            member.filename = name
            member.create_system = 3
            member.external_attr = mode << 16
            bundle.writestr(member, content)
    return path


class ArchiveDownload:
    """Serve one explicit archive and fail unexpected or repeated requests."""

    def __init__(self, url: str, archive: Path) -> None:
        self.url = url
        self.archive = archive
        self.requested = False

    def __call__(self, url: str, destination: Path) -> None:
        if url != self.url or self.requested:
            raise AssertionError(f"unexpected download: {url}")
        self.requested = True
        shutil.copyfile(self.archive, destination)
