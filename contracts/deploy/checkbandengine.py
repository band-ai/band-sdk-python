"""Verify the exact historical engine required by Band's current room API."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def verify(root: Path) -> str:
    root = Path(root)
    source = json.loads((root / "SOURCE.json").read_text())
    if source["revision"] != "ca1147db8121297eeea7e70db13f0d41d8fee44f":
        raise ValueError("Band engine revision changed")
    for name, expected in source["files"].items():
        path = root / name
        if (
            path.is_symlink()
            or not path.is_relative_to(root)
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("Band engine source hash mismatch: " + name)
    actual = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
        and not any(part in ("_build", "deps") for part in path.relative_to(root).parts)
    }
    if actual != set(source["files"]) | {"SOURCE.json", ".gitignore"}:
        raise ValueError("Band engine source inventory changed")
    return source["revision"]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logger.info(
        "Verified engine %s",
        verify(Path(__file__).resolve().parents[1] / "engines/band-v1"),
    )
