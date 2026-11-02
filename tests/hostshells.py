"""The shells a coding agent may run a command line in on each CI host.

``None`` is the host default (``/bin/sh`` or ``cmd.exe``); Windows agents may
also use either PowerShell.
"""

from __future__ import annotations

import sys

# Bounds a whole shell lifetime, including initialization on a fresh runner.
SHELL_PROCESS_TIMEOUT_SECONDS = 30

HOST_SHELLS: tuple[str | None, ...] = (
    (None, "pwsh", "powershell") if sys.platform == "win32" else (None,)
)


def host_shell_id(shell: str | None) -> str:
    return shell or "host"


def shell_argv(shell: str, command: str) -> list[str]:
    """Run ``command`` as one PowerShell command line."""
    return [shell, "-NoProfile", "-NonInteractive", "-Command", command]
