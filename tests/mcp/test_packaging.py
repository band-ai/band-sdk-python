"""band-mcp's published dependencies stay installable next to band-sdk."""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement

from tests.paths import BAND_MCP_DIR, REPO_ROOT


def _requirements(project_dir: Path) -> dict[str, Requirement]:
    pyproject = tomllib.loads((project_dir / "pyproject.toml").read_text())
    return {
        (req := Requirement(spec)).name: req
        for spec in pyproject["project"]["dependencies"]
    }


def _exact_pins(project_dir: Path) -> set[str]:
    return {
        name
        for name, req in _requirements(project_dir).items()
        if any(spec.operator == "==" for spec in req.specifier)
    }


def test_band_mcp_never_repins_what_band_sdk_pins_exactly() -> None:
    """band-mcp installs next to whichever band-sdk satisfies its floor, so a
    second exact pin of the same package drifts from that band-sdk's and makes
    the published wheel uninstallable -- band-sdk alone owns the exact pin."""
    band_mcp = _requirements(BAND_MCP_DIR)

    repinned = {
        name: str(band_mcp[name].specifier)
        for name in _exact_pins(REPO_ROOT)
        if name in band_mcp and band_mcp[name].specifier
    }

    assert repinned == {}
