"""Run workflow shell steps using Git Bash on Windows and native Bash elsewhere."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def run_bash(
    script: str, *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Execute a shell script the same way a workflow's bash step runs it."""
    bash = "bash"
    if sys.platform == "win32":
        bash = str(Path(os.environ["ProgramFiles"]) / "Git" / "bin" / "bash.exe")

    return subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        check=False,
    )
