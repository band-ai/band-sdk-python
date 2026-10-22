"""Verify the exact sealed release inventory before using any artifact."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile


@dataclass(frozen=True)
class VerifiedRelease:
    """Describe artifacts verified before their dependencies are installed."""

    root: Path
    manifest: dict[str, Any]
    wheel: Path


def verify_sdk_wheel(path: Path) -> None:
    """Require the canonical distribution inside the sealed installable wheel."""
    if not path.name.startswith("band_sdk-"):
        raise ValueError("Release requires the canonical band-sdk wheel")
    try:
        with ZipFile(path) as archive:
            metadata = [
                item
                for item in archive.infolist()
                if item.filename.endswith(".dist-info/METADATA")
            ]
            if len(metadata) != 1 or metadata[0].file_size > 262144:
                raise ValueError("Release requires unambiguous band-sdk wheel metadata")
            message = BytesParser().parsebytes(archive.read(metadata[0]))
    except (BadZipFile, KeyError, OSError) as error:
        raise ValueError("Release requires readable band-sdk wheel metadata") from error
    name = re.sub(r"[-_.]+", "-", message.get("Name", "")).lower()
    if name != "band-sdk":
        raise ValueError("Release wheel metadata must identify band-sdk")


def verify_release(directory: str | Path) -> VerifiedRelease:
    root = Path(directory).absolute()
    if root.is_symlink():
        raise ValueError("Release symlink rejected: " + str(root))
    if not root.is_dir():
        raise ValueError("Release directory required: " + str(root))
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Release symlink rejected: " + str(path))
        mode = path.stat().st_mode
        if stat.S_ISREG(mode):
            actual.add(path.relative_to(root).as_posix())
        elif not stat.S_ISDIR(mode):
            raise ValueError("Release nonregular artifact rejected: " + str(path))
    try:
        manifest = json.loads((root / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("Invalid release manifest") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("profile") != "band-contract/release/1"
        or manifest.get("source_dirty") is not False
    ):
        raise ValueError("Sealed clean source release required")
    files, modes = manifest.get("files"), manifest.get("modes")
    if (
        not isinstance(files, dict)
        or not isinstance(modes, dict)
        or set(files) != set(modes)
    ):
        raise ValueError("Release manifest files/modes inventory mismatch")
    for name in files:
        path = PurePosixPath(name)
        if (
            not name
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or path.as_posix() != name
            or name == "manifest.json"
        ):
            raise ValueError("Invalid release artifact path: " + name)
    if actual != set(files) | {"manifest.json"}:
        raise ValueError(
            "Release file inventory mismatch: missing="
            + repr(sorted((set(files) | {"manifest.json"}) - actual))
            + "; extra="
            + repr(sorted(actual - set(files) - {"manifest.json"}))
        )
    wheels = [
        name
        for name in files
        if PurePosixPath(name).suffix == ".whl"
        and PurePosixPath(name).parent == PurePosixPath("wheels")
    ]
    if len(wheels) != 1:
        raise ValueError("Release requires exactly one wheel in wheels/")
    if any(
        PurePosixPath(name).suffix == ".whl"
        and PurePosixPath(name).parent
        not in (PurePosixPath("wheels"), PurePosixPath("dependencies"))
        for name in files
    ):
        raise ValueError("Release wheels must be in wheels/ or dependencies/")
    for name, digest in files.items():
        path = root / name
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("Release content mismatch: " + name)
        mode = modes[name]
        if type(mode) is not int or path.stat().st_mode & 0o777 != mode:
            raise ValueError("Release permission mismatch: " + name)
    wheel = root / wheels[0]
    verify_sdk_wheel(wheel)
    return VerifiedRelease(root, manifest, wheel)
