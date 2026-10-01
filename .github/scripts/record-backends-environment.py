#!/usr/bin/env python
"""Record the backends lane's environment for the scorecard evidence.

Writes backend CLI versions and non-secret run metadata.
"""

from __future__ import annotations

import json
import pathlib
import platform
import shutil
import subprocess
import tempfile

from band.integrations.acp.cursor import CURSOR_CLI_BINARY
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.deps import cli_binary

# This runs in an always() step ahead of the scorecard uploads; a stalled CLI
# must not hold it until the job timeout.
VERSION_TIMEOUT_S = 30


def cli_version(binary: str) -> str:
    if (cli := shutil.which(binary)) is None:
        return "not found"
    # A resolved local path plus a literal flag, so shell=True carries no
    # injection risk; it lets Windows .cmd shims launch through cmd.exe.
    # A file, not a pipe: the timeout kills only the shell, and on Windows a pipe
    # still held by the shim's grandchild would block run() past the timeout.
    with tempfile.TemporaryFile("w+") as output:
        try:
            completed = subprocess.run(
                f'"{cli}" --version',
                shell=True,
                stdout=output,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
                timeout=VERSION_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return f"timed out after {VERSION_TIMEOUT_S}s"
        output.seek(0)
        version = output.read().strip()
    return version if completed.returncode == 0 else f"exited {completed.returncode}"


def main() -> None:
    settings = BaselineSettings()
    metadata = {
        "copilot_cli": cli_version("copilot"),
        "cursor_cli": cli_version(
            cli_binary(settings.backends.cursor_command, CURSOR_CLI_BINARY)
        ),
        "os": platform.platform(),
        "copilot_auth": {
            "mode": "byok",
            "provider": "anthropic",
            "model": settings.llm_models.anthropic_model,
        },
    }
    # OS-qualified: the scorecard job merges every OS's artifact for this lane
    # into one directory, where same-named files would overwrite each other.
    out = pathlib.Path(f"artifacts/environment-backends-{platform.system()}.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    main()
