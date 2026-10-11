#!/usr/bin/env python3
"""Install the released v3 TCK, verifying its published archive checksum."""

from __future__ import annotations

import argparse
import hashlib
import io
import logging
import platform
import tarfile
import urllib.request
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)
VERSION = "3.0.0-m.8"
RELEASE = f"https://github.com/docker/sandbox-kit-spec/releases/download/v{VERSION}"


def install(destination: Path) -> None:
    system = platform.system().lower()
    architecture = {
        "x86_64": "amd64",
        "AMD64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(platform.machine())
    if system not in {"linux", "darwin", "windows"} or architecture is None:
        raise ValueError("unsupported TCK platform")
    suffix = "zip" if system == "windows" else "tar.gz"
    archive_name = f"kit-tck_{VERSION}_{system}_{architecture}.{suffix}"
    with urllib.request.urlopen(f"{RELEASE}/checksums.txt", timeout=60) as response:
        checksums = response.read().decode()
    expected = next(
        (
            line.split()[0]
            for line in checksums.splitlines()
            if line.split()[-1].lstrip("*") == archive_name
        ),
        None,
    )
    if expected is None:
        raise ValueError("release checksum is missing")
    with urllib.request.urlopen(f"{RELEASE}/{archive_name}", timeout=60) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != expected:
        raise ValueError("TCK archive checksum mismatch")
    executable = "kit-tck.exe" if system == "windows" else "kit-tck"
    if suffix == "zip":
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            content = bundle.read(executable)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            member = bundle.extractfile(executable)
            if member is None:
                raise ValueError("archive lacks TCK executable")
            content = member.read()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    destination.chmod(0o755)
    logger.info("Installed kit-tck %s at %s", VERSION, destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    install(args.destination)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
