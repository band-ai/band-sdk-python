"""Create sealed service fixtures and observe controlled host operations."""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path

from tests.contracts.release import kit


class StartCaptured(Exception):
    """Stop the synthetic host before launching a real service."""


ROOM_PROTECTIONS = {
    "RestrictNamespaces": "yes",
    "ProtectKernelTunables": "yes",
    "ProtectKernelModules": "yes",
    "ProtectKernelLogs": "yes",
    "ProtectControlGroups": "yes",
    "ProtectClock": "yes",
    "ProtectHostname": "yes",
    "RestrictRealtime": "yes",
    "CapabilityBoundingSet": "",
    "AmbientCapabilities": "",
    "SystemCallFilter": "@system-service",
    "SystemCallErrorNumber": "1",
}


def sealed_fixture(release: Path) -> None:
    kit.wheel(release / "wheels/band_sdk-6.0.1-py3-none-any.whl")
    (release / "requirements-runtime.lock").write_text("# No fixture dependencies\n")
    kit.seal(release)
    manifest = json.loads((release / "manifest.json").read_text())
    manifest["source_commit"] = "fixture"
    (release / "manifest.json").write_text(json.dumps(manifest))


def mock_host_copy(command: Sequence[str]) -> None:
    """Copy only the owned fixture; retain real source and copied-bundle verification."""
    if command[:2] == ["install", "-d"]:
        Path(command[-1]).mkdir(parents=True, exist_ok=True)
    elif command[:2] == ["cp", "-a"]:
        shutil.copytree(command[2], command[3])
