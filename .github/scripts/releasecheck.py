"""Validate shared release metadata and exact SDK wheel requirements."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import tomllib
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

logger = logging.getLogger(__name__)


def sdk_floor(requirements: list[str]) -> str:
    """Read the single supported SDK lower bound, failing on ambiguous metadata."""
    parsed = [Requirement(value) for value in requirements]
    matches = [
        requirement
        for requirement in parsed
        if canonicalize_name(requirement.name) == "band-sdk"
    ]
    if len(matches) != 1 or matches[0].marker or matches[0].url:
        raise ValueError("Expected one unconditional band-sdk dependency")
    specs = list(matches[0].specifier)
    if len(specs) != 1 or specs[0].operator != ">=":
        raise ValueError("Expected a single band-sdk>=VERSION floor")
    return str(Version(specs[0].version))


def wheel_floor(wheel: Path) -> str:
    with ZipFile(wheel) as archive:
        metadata_files = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_files) != 1:
            raise ValueError("Expected one wheel METADATA file")
        metadata = BytesParser().parsebytes(archive.read(metadata_files[0]))
    return sdk_floor(metadata.get_all("Requires-Dist", []))


def shared_policy(root: Path) -> bool:
    config = json.loads((root / "release-please-config.json").read_text())
    return any(
        plugin.get("type") == "linked-versions"
        and set(plugin.get("components", [])) == {"band-sdk", "band-mcp"}
        for plugin in config.get("plugins", [])
        if isinstance(plugin, dict)
    )


def check_release(root: Path, previous: dict[str, str] | None = None) -> None:
    """Validate candidate equality and advancement against the last release."""
    if not shared_policy(root):
        return
    sdk = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    mcp = tomllib.loads((root / "packages/band-mcp/pyproject.toml").read_text())[
        "project"
    ]
    manifest = json.loads((root / ".release-please-manifest.json").read_text())
    if set(manifest) != {".", "packages/band-mcp"}:
        raise ValueError(f"Expected both release manifest entries: {manifest}")
    versions = {
        "sdk": sdk["version"],
        "mcp": mcp["version"],
        "floor": sdk_floor(mcp["dependencies"]),
        **manifest,
    }
    if len({Version(value) for value in versions.values()}) != 1:
        raise ValueError(f"Shared release versions disagree: {versions}")
    if previous and any(
        Version(sdk["version"]) <= Version(value) for value in previous.values()
    ):
        raise ValueError(
            f"Release {sdk['version']} must exceed previous releases {previous}"
        )
    logger.info("Shared release metadata agrees: %s", sdk["version"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--previous-ref")
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.wheel:
        floor = wheel_floor(args.wheel)
        if not args.output:
            raise ValueError("--wheel requires --output")
        args.output.write_text(f"band-sdk=={floor}\n")
        return
    previous = None
    if args.previous_ref:
        previous = json.loads(
            subprocess.check_output(
                ["git", "show", f"{args.previous_ref}:.release-please-manifest.json"],
                cwd=args.root,
                text=True,
            )
        )
    check_release(args.root, previous)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
