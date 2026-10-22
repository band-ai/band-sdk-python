"""Drive release entry points while trapping installation side effects."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch


def arguments(name: str, release: Path, state: Path) -> list[str]:
    """Select the public arguments for each release drill."""
    match name:
        case "smokerelease":
            return [str(release)]
        case "qualifylifecycle":
            return [
                "--baseline",
                str(release),
                "--candidate",
                str(release),
                "--state",
                str(state),
            ]
        case _:
            return ["--release", str(release), "--state", str(state)] + (
                ["--platform", str(release)] if name == "qualifyband" else []
            )


def call_entrypoint(name: str, release: Path, state: Path) -> None:
    """Require inventory validation before creating state or an interpreter."""
    module = importlib.import_module("contracts.deploy." + name)
    with (
        patch.object(sys, "argv", [name, *arguments(name, release, state)]),
        patch("venv.create", side_effect=RuntimeError("unsafe install reached")),
    ):
        if name == "qualifysystemd":
            with patch.object(
                Path, "mkdir", side_effect=RuntimeError("unsafe state creation reached")
            ):
                module.main()
        else:
            with patch.object(module, "PORTS", [], create=True):
                module.main()
