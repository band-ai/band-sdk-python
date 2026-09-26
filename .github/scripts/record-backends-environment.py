#!/usr/bin/env python
"""Record the backends lane's environment for the scorecard evidence.

Writes the validated Copilot CLI version, the OS and the BYOK model, read from
the same settings the builder uses so the record can't drift from the run.
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


def main() -> None:
    settings = BaselineSettings()
    metadata = {
        "copilot_cli": copilot_cli_version(),
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
