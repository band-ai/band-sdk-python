"""Historical MCP tags can validate wheel floors without new repository scripts."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from tests.paths import REPO_ROOT


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
    ) -> None:
        wheel = wheel_index("band_mcp", "2.2.15", requirements)
        (tmp_path / "dist").mkdir()
        wheel.rename(tmp_path / "dist" / wheel.name)
        workflow = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/band-mcp-publish.yml").read_text()
        )
        step = next(
            step
            for step in workflow["jobs"]["build"]["steps"]
            if step["name"]
            == "Wait for the declared band-sdk floor to be indexed on PyPI"
        )
        script = step["run"].split("# Dry-run")[0]
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=tmp_path,
            env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert (result.returncode == 0) is success, result.stderr

    def test_historical_floor_constraint_is_written(
        self, tmp_path: Path, wheel_index: Callable[..., Path]
    ) -> None:
        wheel = wheel_index("band_mcp", "2.2.15", ("band-sdk>=9.0.2",))
        (tmp_path / "dist").mkdir()
        wheel.rename(tmp_path / "dist" / wheel.name)
        workflow = yaml.safe_load(
            (REPO_ROOT / ".github/workflows/band-mcp-publish.yml").read_text()
        )
        step = next(
            step
            for step in workflow["jobs"]["build"]["steps"]
            if step["name"]
            == "Wait for the declared band-sdk floor to be indexed on PyPI"
        )
        subprocess.run(
            ["bash", "-c", step["run"].split("# Dry-run")[0]],
            cwd=tmp_path,
            env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
            check=True,
        )
        assert (tmp_path / "mcp-sdk-floor.txt").read_text() == "band-sdk==9.0.2\n"
