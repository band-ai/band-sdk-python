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
    command = (
        settings.backends.cursor_command.split()
        if settings.backends.cursor_command.strip()
        else ["agent", "acp"]
    )
    if command[-1] == "acp":
        command[-1] = "--version"
    else:
        command.append("--version")
    if (cli := shutil.which(command[0])) is None:
        return "unavailable"
    command[0] = cli
    invocation: str | list[str] = command
    if pathlib.Path(cli).suffix.lower() in {".cmd", ".bat"}:
        invocation = subprocess.list2cmdline(command)
    completed = subprocess.run(
        invocation,
        shell=isinstance(invocation, str),
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
