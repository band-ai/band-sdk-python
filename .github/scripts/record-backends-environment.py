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

from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.toolkit.deps import cli_binary


def copilot_cli_version() -> str:
    if (cli := shutil.which("copilot")) is None:
        return "unavailable"
    # On Windows the npm shim is copilot.cmd, a batch file CreateProcess can't
    # run without cmd.exe; the command is a resolved local path plus a literal
    # flag, so shell=True carries no injection risk.
    completed = subprocess.run(
        f'"{cli}" --version', shell=True, capture_output=True, text=True, check=False
    )
    return completed.stdout.strip()


def cursor_cli_version(settings: BaselineSettings) -> str:
    binary = cli_binary(settings.backends.cursor_command, "agent")
    if (cli := shutil.which(binary)) is None:
        return "unavailable"
    command: str | list[str] = [cli, "--version"]
    if pathlib.Path(cli).suffix.lower() in {".cmd", ".bat"}:
        command = f'"{cli}" --version'
    completed = subprocess.run(
        command,
        shell=isinstance(command, str),
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


def main() -> None:
    settings = BaselineSettings()
    metadata = {
        "copilot_cli": copilot_cli_version(),
        "cursor_cli": cursor_cli_version(settings),
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
