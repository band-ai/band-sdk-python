"""MCP verification resolves its exact SDK floor even beside newer versions."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest


class TestExactFloorResolution:
    @pytest.mark.parametrize(
        "available,success",
        [(("9.3.0",), True), (("9.4.0",), False), (("9.3.0", "9.4.0"), True)],
    )
    def test_exact_floor_cannot_be_substituted(
        self,
        tmp_path: Path,
        wheel_index: Callable[..., Path],
        publisher_install: Callable[[Path], subprocess.CompletedProcess[str]],
        available: tuple[str, ...],
        success: bool,
    ) -> None:
        for version in available:
            wheel_index("band_sdk", version)
        (tmp_path / "dist").mkdir()
        mcp = wheel_index("band_mcp", "9.3.0", ("band-sdk>=9.3.0",))
        mcp = mcp.rename(tmp_path / "dist" / mcp.name)
        result = publisher_install(mcp)
        assert (result.returncode == 0) is success, result.stderr

    def test_missing_floor_recovers_after_exact_wheel_arrives(
        self,
        tmp_path: Path,
        wheel_index: Callable[..., Path],
        publisher_install: Callable[[Path], subprocess.CompletedProcess[str]],
    ) -> None:
        (tmp_path / "dist").mkdir()
        mcp = wheel_index("band_mcp", "9.3.0", ("band-sdk>=9.3.0",))
        mcp = mcp.rename(tmp_path / "dist" / mcp.name)
        first = publisher_install(mcp)
        assert first.returncode != 0
        wheel_index("band_sdk", "9.3.0")
        recovered = publisher_install(mcp)
        assert recovered.returncode == 0, recovered.stderr
        installed = subprocess.run(
            [
                "uv",
                "pip",
                "list",
                "--python",
                str(tmp_path / "band-mcp-verify"),
                "--format",
                "json",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        assert json.loads(installed.stdout) == [
            {"name": "band-mcp", "version": "9.3.0"},
            {"name": "band-sdk", "version": "9.3.0"},
        ]
