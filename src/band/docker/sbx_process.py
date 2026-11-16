"""Shared `sbx`-subprocess execution and output parsing.

`src/` code cannot import from `tests/`, so the logic both sides need lives
here once:

- `run_sbx_subprocess` runs a CLI subprocess and redacts any secret from a
  failure's error message: `band.docker.provision` (the live CLI) and the
  docker helpers in `tests/docker/toolkit/docker_cli.py`.
- `sbx_json` reads `sbx`'s `--json` output: `band.docker.provision`,
  `tests/docker/toolkit/sbx_cli.py`, the self-registration example, and the
  docker demo's and staging smoke's shell scripts.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
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
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an `sbx` subprocess; raise RuntimeError naming the command and its
    stderr/stdout on non-zero exit. `redact`, if given (a secret whether
    piped via `input` or embedded in `argv`), is stripped from the raised
    message should it ever surface in `sbx`'s own output. `env` adds to the
    inherited environment, keeping secrets out of `argv`.
    """
    try:
        result = subprocess.run(
            argv,
            input=input,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=None if env is None else {**os.environ, **env},
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
