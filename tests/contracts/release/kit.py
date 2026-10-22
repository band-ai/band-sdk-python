"""Build sealed release fixtures with real wheel metadata."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile, ZipInfo


def wheel(
    path: Path,
    distribution: str = "band-sdk",
    *,
    requirements: tuple[str, ...] = (),
    modules: dict[str, str] | None = None,
) -> Path:
    """Create an installable wheel with sealed metadata and optional modules."""
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = distribution.replace("-", "_") + "-6.0.1.dist-info"
    contents = {
        metadata + "/METADATA": (
            f"Metadata-Version: 2.3\nName: {distribution}\nVersion: 6.0.1\n"
            + "".join(f"Requires-Dist: {requirement}\n" for requirement in requirements)
        ),
        metadata + "/WHEEL": (
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        **(modules or {}),
    }
    records = io.StringIO(newline="")
    writer = csv.writer(records)
    with ZipFile(path, "w") as archive:
        for name, source in contents.items():
            encoded = source.encode("utf-8")
            digest = base64.urlsafe_b64encode(hashlib.sha256(encoded).digest())
            writer.writerow(
                (name, "sha256=" + digest.rstrip(b"=").decode(), len(encoded))
            )
            archive.writestr(ZipInfo(name), encoded)
        writer.writerow((metadata + "/RECORD", "", ""))
        archive.writestr(ZipInfo(metadata + "/RECORD"), records.getvalue())
    path.chmod(0o644)
    return path


def seal(root: Path) -> None:
    """Record the actual bytes and modes of a release fixture."""
    files = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "profile": "band-contract/release/1",
                "source_dirty": False,
                "files": files,
                "modes": {name: (root / name).stat().st_mode & 0o777 for name in files},
            }
        ),
        encoding="utf-8",
    )


def release(root: Path, distribution: str = "band-sdk") -> Path:
    """Create the SDK wheel and a pinned engine in a sealed fixture."""
    root.mkdir()
    name = distribution.replace("-", "_") + "-6.0.1-py3-none-any.whl"
    wheel(root / "wheels" / name, distribution)
    engine = root / "engine/bin/contract-engine"
    engine.parent.mkdir(parents=True)
    engine.write_bytes(b"engine")
    engine.chmod(0o755)
    (root / "requirements-runtime.lock").write_text("# No fixture dependencies\n")
    seal(root)
    return root
