"""Historical MCP tags can validate wheel floors without new repository scripts."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest


class TestHistoricalDispatch:
    @pytest.mark.parametrize(
        "requirements,success",
        [(("band-sdk>=9.0.2",), True), (("band-sdk>=9.0.2,<10",), False), ((), False)],
    )
    def test_inline_metadata_path_needs_no_new_script(
        self,
        tmp_path: Path,
        wheel_index: Callable[..., Path],
        requirements: tuple[str, ...],
        success: bool,
        historical_floor_script: str,
    ) -> None:
        wheel = wheel_index("band_mcp", "2.2.15", requirements)
        (tmp_path / "dist").mkdir()
        wheel.rename(tmp_path / "dist" / wheel.name)
        result = subprocess.run(
            ["bash", "-c", historical_floor_script],
            cwd=tmp_path,
            env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert (result.returncode == 0) is success, result.stderr

    def test_historical_floor_constraint_is_written(
        self,
        tmp_path: Path,
        wheel_index: Callable[..., Path],
        historical_floor_script: str,
    ) -> None:
        wheel = wheel_index("band_mcp", "2.2.15", ("band-sdk>=9.0.2",))
        (tmp_path / "dist").mkdir()
        wheel.rename(tmp_path / "dist" / wheel.name)
        subprocess.run(
            ["bash", "-c", historical_floor_script],
            cwd=tmp_path,
            env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
            check=True,
        )
        assert (tmp_path / "mcp-sdk-floor.txt").read_text() == "band-sdk==9.0.2\n"
