"""Shared `sbx`-subprocess execution and output parsing.

Both `band.docker.provision` (the live CLI) and the kit's E2E toolkit under
`tests/docker/toolkit/` run `sbx` subprocesses, redact any secret from a
failure's error message, and read `sbx`'s `--json` output -- `src/` code cannot
import from `tests/`, so this is the one place both import that logic from
instead of hand-duplicating it.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any


def sbx_json(stdout: str) -> Any:
    """The document an `sbx ... --json` command printed.

    At most once per 24h, when a newer release exists, `sbx` appends a
    human-readable "Update Available" box to stdout after the document
    (verified against v0.47.0), so only the leading JSON value is the contract.
    """
    document, _ = json.JSONDecoder().raw_decode(stdout.lstrip())
    return document


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
    try:
        result = subprocess.run(
            argv,
            input=input,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        command = " ".join(argv)
        if redact:
            command = command.replace(redact, "***")
        raise RuntimeError(f"{command} timed out after {timeout}s") from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        command = " ".join(argv)
        if redact:
            detail = detail.replace(redact, "***")
            command = command.replace(redact, "***")
        raise RuntimeError(f"{command} failed (exit {result.returncode}): {detail}")
    return result
