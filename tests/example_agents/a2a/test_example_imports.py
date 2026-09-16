"""Import smoke tests for standalone A2A examples."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.paths import EXAMPLES_ROOT, REPO_ROOT

EXAMPLE_CASES = (
    (
        EXAMPLES_ROOT / "a2a_gateway" / "02_with_demo_agent.py",
        EXAMPLES_ROOT / "a2a_gateway",
    ),
    (
        EXAMPLES_ROOT / "a2a_gateway" / "demo_orchestrator" / "__main__.py",
        EXAMPLES_ROOT / "a2a_gateway",
    ),
    (
        EXAMPLES_ROOT / "mixed" / "03_fact_checker_a2a.py",
        EXAMPLES_ROOT / "mixed",
    ),
    (
        EXAMPLES_ROOT / "mixed" / "04_risk_reviewer_a2a.py",
        EXAMPLES_ROOT / "mixed",
    ),
)


@pytest.mark.parametrize(("example", "import_root"), EXAMPLE_CASES)
def test_a2a_example_imports(example: Path, import_root: Path) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(import_root)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import runpy; "
                f"runpy.run_path({str(example)!r}, run_name='example_import')"
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
