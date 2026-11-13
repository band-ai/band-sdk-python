"""MCP verification resolves its exact SDK floor even beside newer versions."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

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
        releasecheck: ModuleType,
        available: tuple[str, ...],
        success: bool,
    ) -> None:
        for version in available:
            wheel_index("band_sdk", version)
        mcp = wheel_index("band_mcp", "9.3.0", ("band-sdk>=9.3.0",))
        floor = releasecheck.wheel_floor(mcp)
        environment = tmp_path / "env"
        subprocess.run(
            ["uv", "venv", str(environment), "--python", sys.executable],
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            [
                "uv",
                "pip",
                "install",
                str(mcp),
                f"band-sdk=={floor}",
                "--no-index",
                "--find-links",
                str(tmp_path),
                "--python",
                str(environment),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert (result.returncode == 0) is success, result.stderr

    def test_missing_floor_recovers_after_exact_wheel_arrives(
        self, tmp_path: Path, wheel_index: Callable[..., Path], releasecheck: ModuleType
    ) -> None:
        mcp = wheel_index("band_mcp", "9.3.0", ("band-sdk>=9.3.0",))
        floor = releasecheck.wheel_floor(mcp)
        environment = tmp_path / "env"
        subprocess.run(
            ["uv", "venv", str(environment), "--python", sys.executable],
            check=True,
            capture_output=True,
        )
        command = [
            "uv",
            "pip",
            "install",
            str(mcp),
            f"band-sdk=={floor}",
            "--no-index",
            "--find-links",
            str(tmp_path),
            "--python",
            str(environment),
        ]
        first = subprocess.run(command, capture_output=True, check=False)
        assert first.returncode != 0
        wheel_index("band_sdk", floor)
        recovered = subprocess.run(command, capture_output=True, check=False)
        assert recovered.returncode == 0, recovered.stderr
