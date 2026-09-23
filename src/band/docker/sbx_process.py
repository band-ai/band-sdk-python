"""Shared `sbx`-subprocess execution.

Both `band.docker.provision` (the live CLI) and
`tests.docker.toolkit.sbx_cli` (the never-in-VM E2E toolkit) run `sbx`
subprocesses and must redact any secret from a failure's error message --
`src/` code cannot import from `tests/`, so this is the one place both
directions import the run+redact logic from instead of hand-duplicating it.
"""

from __future__ import annotations

import subprocess


def run_sbx_subprocess(
    argv: list[str],
    *,
    timeout: int,
    input: str | None = None,
    redact: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an `sbx` subprocess; raise RuntimeError naming the command and its
    stderr/stdout on non-zero exit. `redact`, if given (a secret whether
    piped via `input` or embedded in `argv`), is stripped from the raised
    message should it ever surface in `sbx`'s own output.
    """
    result = subprocess.run(
        argv,
        input=input,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        command = " ".join(argv)
        if redact:
            detail = detail.replace(redact, "***")
            command = command.replace(redact, "***")
        raise RuntimeError(f"{command} failed (exit {result.returncode}): {detail}")
    return result
